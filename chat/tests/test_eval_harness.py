"""The scorer, tested without a model.

An evaluation suite that scores wrongly is worse than none: it reports a number
nobody can act on, and the first time it disagrees with reality people stop trusting
the number rather than the system. These check the parts that decide pass or fail -
number extraction, tolerance, refusal detection - so the figures the suite prints
mean what they say.

Fast and deterministic, unlike the suite they score. These belong in CI; the
evaluation itself does not.
"""

from decimal import Decimal

from evals.cases import CASES
from evals.run import Result, _looks_like_refusal, _numbers, _states, scorecard


def test_a_figure_written_with_separators_is_recognised():
    assert _states("Total payroll is $1,268,431.55.", Decimal("1268431.55"))


def test_a_rounded_figure_is_within_tolerance():
    """ "about $1,268,432" has answered the question. Scoring it wrong would measure
    formatting rather than accuracy."""
    assert _states("about $1,268,432", Decimal("1268431.55"))


def test_a_figure_in_millions_is_recognised():
    assert _states("roughly $1.27 million", Decimal("1268431.55"))


def test_a_wrong_figure_fails():
    assert not _states("Total payroll is $4,200,000.00.", Decimal("1268431.55"))


def test_a_figure_outside_tolerance_fails():
    """1% out is wrong. The tolerance exists for rounding, not approximation."""
    assert not _states("$1,281,000", Decimal("1268431.55"))


def test_zero_is_matched_exactly():
    assert _states("There are 0 employees.", Decimal("0"))


def test_numbers_are_pulled_out_of_prose():
    assert _numbers("42 people earning $1,000.50") == [Decimal("42"), Decimal("1000.50")]


def test_the_standard_refusal_is_detected():
    from app.services.scope import REFUSAL

    assert _looks_like_refusal(REFUSAL)


def test_an_answer_is_not_mistaken_for_a_refusal():
    assert not _looks_like_refusal("ACME has 9,483 employees on the payroll.")


def _result(**overrides) -> Result:
    base = {
        "case": CASES[0],
        "answer": "",
        "used_tools": [],
        "figures": [],
        "seconds": 1.0,
        "checks": {},
    }
    base.update(overrides)
    return Result(**base)


def test_the_scorecard_counts_what_passed():
    report = scorecard([_result(checks={"a": True}), _result(checks={"a": False})])

    assert report["total"] == 2
    assert report["passed"] == 1
    assert report["accuracy"] == 0.5
    assert report["checks"]["a"]["rate"] == 0.5


def test_a_case_with_one_failing_check_does_not_pass():
    """Every check that applies has to hold. A right number reached with the wrong
    tool is luck, and luck does not survive a model upgrade."""
    assert not _result(checks={"tool": True, "numeric": False}).passed


def test_an_errored_case_never_passes():
    assert not _result(checks={}, error="boom").passed


def test_every_case_has_a_unique_id():
    ids = [case.id for case in CASES]

    assert len(ids) == len(set(ids))


def test_every_numeric_case_knows_how_to_fetch_its_truth():
    """A case with a truth function but the wrong fetch scores against the wrong
    filter - "payroll in India" checked against the company total."""
    for case in CASES:
        if case.truth is not None:
            assert case.fetch[0].startswith("/"), case.id


def test_refusal_cases_expect_no_tools():
    for case in CASES:
        if case.expect_refusal:
            assert not case.expects_tools, case.id


def test_every_truth_function_resolves_against_a_real_payload():
    """A truth function that reads a key the API does not have returns None and the
    case scores as "no figure expected" - silently, and forever.

    This is the shape the dashboard actually returns; the fixture is checked against
    the live service by the evaluation itself, but the key names are pinned here so
    a rename fails fast and loudly.
    """
    payload = {
        "employee_count": 19483,
        "total_payroll_usd": "1916868391.00",
        "average_salary_usd": "98386.72",
        "median_salary_usd": "84341.00",
        "highest": {"usd_amount": "425495.00"},
        "lowest": {"usd_amount": "10.00"},
    }

    for case in CASES:
        if case.truth is not None:
            assert case.truth(payload) is not None, case.id
