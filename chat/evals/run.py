"""Run the golden set against the real model and the real database.

    python -m evals.run                 # all cases, prints a scorecard
    python -m evals.run --tag refusal   # one slice
    python -m evals.run --json out.json # machine-readable, for CI or a trend line

This is not a unit test and must not become one. It costs money (or quota), needs a
network, and it is *non-deterministic*: the same question can score 10/10 today and
9/10 tomorrow with no code change at all. A suite that fails at random is a suite
people start ignoring, and the day it fails for a real reason nobody looks.

So the unit tests prove the harness - which tool ran, what was refused, that an
ungrounded answer is discarded - with the model faked. This proves the *system*,
model included, and is run deliberately.

## How a case is scored

Each check is independent, because "was it right" is several questions:

| Check | Fails when |
|---|---|
| `tool` | It used no tool the case named, so a right answer was luck |
| `numeric` | The figure in the prose is not the figure in the database |
| `refusal` | It answered what it should decline, or declined what it should answer |
| `grounded` | It stated a figure no tool returned |

A case passes only if every check that applies to it passes. The headline number is
the share of cases that passed, and the per-check rates are printed beside it,
because a suite at 80% behaves very differently depending on which 20% failed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from groq import RateLimitError as RateLimited
from langchain_groq import ChatGroq

from app.access import Principal
from app.clients.salary import SalaryClient
from app.config import get_settings
from app.knowledge import as_context, search
from app.services import conversation
from evals.cases import CASES, Case

# How far a stated figure may be from the truth and still count. Not zero: a model
# reporting "$1.27 million" for 1,268,431.55 has answered the question correctly,
# and scoring that as a failure measures formatting rather than accuracy.
TOLERANCE = Decimal("0.005")  # 0.5%

# Words that mean the assistant declined. Matched loosely on purpose - the exact
# refusal text is allowed to change without rewriting the dataset.
REFUSAL_MARKERS = (
    "i can only answer",
    "i don't have",
    "i do not have",
    "cannot answer",
    "can't answer",
    "rather say so",
    "not able to",
)


@dataclass
class Result:
    case: Case
    answer: str
    used_tools: list[str]
    figures: list[dict]
    seconds: float
    checks: dict[str, bool] = field(default_factory=dict)
    expected: Decimal | None = None
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and all(self.checks.values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", help="only cases with this tag")
    parser.add_argument("--json", metavar="PATH", help="write the full report here")
    parser.add_argument("--repeat", type=int, default=1, help="run each case N times")
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="retries per case when the provider rate-limits us (default 3)",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.model_configured:
        print("GROQ_API_KEY is not set. Put it in devops/.env and restart.", file=sys.stderr)
        return 2

    cases = [c for c in CASES if not args.tag or args.tag in c.tags]
    salary = SalaryClient()
    model = ChatGroq(
        api_key=settings.groq_api_key,
        model=settings.groq_model,
        temperature=0.1,
        max_retries=0,  # the suite does its own backoff, and double-retrying hides quota
    )
    caller = Principal(
        sub="eval-runner",
        email=None,
        display_name="Evaluation",
        roles=frozenset({"hr_manager"}),
    )

    print(f"\nModel: {settings.groq_model}")
    print(f"Cases: {len(cases)}" + (f" x {args.repeat}" if args.repeat > 1 else ""))
    print("Ground truth is read from the database at run time.\n")

    results: list[Result] = []
    for case in cases:
        for _ in range(args.repeat):
            result = run_case(case, model=model, salary=salary, caller=caller, retries=args.retries)
            results.append(result)
            _print_line(result)

    report = scorecard(results)
    _print_scorecard(report, results)

    if args.json:
        with open(args.json, "w") as handle:
            json.dump(_serialisable(report, results), handle, indent=2, default=str)
        print(f"\nFull report: {args.json}")

    # Non-zero when a case failed, so this can gate a release if somebody chooses
    # to. Deliberately not wired into CI by default - see the module docstring.
    return 0 if report["passed"] == report["total"] else 1


def run_case(case: Case, *, model, salary, caller: Principal, retries: int = 3) -> Result:
    """Run one case, waiting out the free tier rather than scoring it as a failure.

    The token budget is per minute and seven tool schemas make every call
    expensive, so a back-to-back run exhausts it. Recording that as a wrong answer
    would make the headline number a measure of quota, not of the assistant.
    """
    expected = _truth(case, salary=salary, caller=caller)

    started = time.perf_counter()
    try:
        answer = _with_backoff(
            lambda: _ask(case, model=model, salary=salary, caller=caller),
            retries=retries,
        )
    except Exception as exc:  # noqa: BLE001 - an eval records failures, it does not raise
        return Result(
            case=case,
            answer="",
            used_tools=[],
            figures=[],
            seconds=time.perf_counter() - started,
            error=f"{type(exc).__name__}: {exc}",
        )

    result = Result(
        case=case,
        answer=answer.text,
        used_tools=answer.used_tools,
        figures=answer.figures,
        seconds=time.perf_counter() - started,
        expected=expected,
    )
    result.checks = _score(result)
    return result


def _with_backoff(call, *, retries: int):
    """Retry only rate limiting, and only as long as the provider asks for.

    Nothing else is retried: a wrong answer retried until it is right is not an
    evaluation, it is a search.
    """
    for attempt in range(retries + 1):
        try:
            return call()
        except RateLimited as exc:
            if attempt == retries:
                raise
            wait = exc.retry_after or (2**attempt)
            print(f"      rate limited, waiting {wait:.0f}s")
            time.sleep(min(wait + 1, 70))
    raise RuntimeError("unreachable")


def _ask(case: Case, *, model, salary, caller: Principal):
    notes = search(case.question)
    return conversation.answer(
        case.question,
        model=model,
        salary=salary,
        caller=caller,
        context=as_context(notes) if notes else None,
    )


def _truth(case: Case, *, salary, caller: Principal) -> Decimal | None:
    """The expected figure, read from the database now - not from the dataset."""
    if case.truth is None:
        return None
    path, params = case.fetch
    return case.truth(salary.get(path, params, caller=caller))


def _score(result: Result) -> dict[str, bool]:
    case = result.case
    checks: dict[str, bool] = {}
    refused = _looks_like_refusal(result.answer)

    if case.expect_refusal:
        checks["refusal"] = refused
        # A refusal that also quotes a figure is not a refusal.
        checks["grounded"] = not _numbers(result.answer) or refused
        return checks

    # Everything below is a question it should have answered.
    checks["answered"] = not refused

    if case.expects_tools:
        checks["tool"] = bool(set(result.used_tools) & case.expects_tools)

    if result.expected is not None:
        checks["numeric"] = _states(result.answer, result.expected)

    # Groundedness: it may only state figures when a tool gave it some. Knowledge
    # cases have no tools and no figures, so the check is whether it stayed
    # qualitative.
    if not result.used_tools and "knowledge" not in case.tags:
        checks["grounded"] = not _numbers(result.answer)

    return checks


def _states(answer: str, expected: Decimal) -> bool:
    """Does the prose contain the expected figure, within tolerance?

    Every number in the answer is considered, because the right one can arrive in
    any phrasing - "$1,268,431.55", "1.27 million", "about 1,268,432".
    """
    for number in _numbers(answer):
        if expected == 0 and number == 0:
            return True
        if expected != 0 and abs(number - expected) / abs(expected) <= TOLERANCE:
            return True
        # "1.27 million" against 1,268,431.55
        for scale in (Decimal(1_000), Decimal(1_000_000), Decimal(1_000_000_000)):
            scaled = number * scale
            if expected != 0 and abs(scaled - expected) / abs(expected) <= TOLERANCE:
                return True
    return False


_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> list[Decimal]:
    out = []
    for raw in _NUMBER.findall(text or ""):
        try:
            out.append(Decimal(raw.replace(",", "")))
        except InvalidOperation:
            continue
    return out


def _looks_like_refusal(answer: str) -> bool:
    lowered = (answer or "").lower()
    return any(marker in lowered for marker in REFUSAL_MARKERS)


def scorecard(results: list[Result]) -> dict:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    per_check: dict[str, list[bool]] = {}
    for result in results:
        for name, ok in result.checks.items():
            per_check.setdefault(name, []).append(ok)

    times = sorted(r.seconds for r in results)
    return {
        "total": total,
        "passed": passed,
        "accuracy": round(passed / total, 3) if total else 0.0,
        "checks": {
            name: {
                "passed": sum(values),
                "total": len(values),
                "rate": round(sum(values) / len(values), 3),
            }
            for name, values in sorted(per_check.items())
        },
        "errors": sum(1 for r in results if r.error),
        "median_seconds": round(times[len(times) // 2], 2) if times else 0.0,
        "slowest_seconds": round(times[-1], 2) if times else 0.0,
    }


def _print_line(result: Result) -> None:
    mark = "PASS" if result.passed else "FAIL"
    failed = [name for name, ok in result.checks.items() if not ok]
    detail = result.error or (", ".join(f"-{name}" for name in failed) if failed else "")
    print(f"  [{mark}] {result.case.id:<26} {result.seconds:5.1f}s  {detail}")


def _print_scorecard(report: dict, results: list[Result]) -> None:
    print("\n" + "=" * 68)
    print(f"  Accuracy: {report['passed']}/{report['total']}  ({report['accuracy'] * 100:.0f}%)")
    print(f"  Median {report['median_seconds']}s, slowest {report['slowest_seconds']}s")
    if report["errors"]:
        print(f"  Errors: {report['errors']}")
    print("\n  By check:")
    for name, stats in report["checks"].items():
        print(f"    {name:<10} {stats['passed']}/{stats['total']}  ({stats['rate'] * 100:.0f}%)")

    failures = [r for r in results if not r.passed]
    if failures:
        print("\n  Failures in detail:")
        for result in failures:
            print(f"\n  - {result.case.id}: {result.case.question}")
            if result.expected is not None:
                print(f"      expected figure : {result.expected}")
            print(f"      tools used      : {result.used_tools or '-'}")
            print(f"      answer          : {_trim(result.answer)}")
            if result.error:
                print(f"      error           : {result.error}")
    print("=" * 68)


def _trim(text: str, limit: int = 200) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + "..."


def _serialisable(report: dict, results: list[Result]) -> dict:
    return {
        "summary": report,
        "cases": [
            {
                "id": r.case.id,
                "question": r.case.question,
                "tags": sorted(r.case.tags),
                "expected": str(r.expected) if r.expected is not None else None,
                "answer": r.answer,
                "used_tools": r.used_tools,
                "checks": r.checks,
                "passed": r.passed,
                "seconds": round(r.seconds, 2),
                "error": r.error,
            }
            for r in results
        ],
    }


if __name__ == "__main__":
    sys.exit(main())
