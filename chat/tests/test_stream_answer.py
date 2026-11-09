"""Streaming an answer without losing the grounding guarantee.

The tension this file exists for: tokens cannot be un-sent. The guard says an
ungrounded answer is never shown - but a naive stream shows "$4.2 million" word by
word and only afterwards discovers no tool produced it.

The resolution is that prose is only released once something grounds it. Until a
tool has returned data (or a reference note is in the prompt), the model's prose is
held. If the turn ends still ungrounded, the held text is discarded and the refusal
is sent instead - and the user never saw the invented figure.

Events are small dicts, so the SSE layer above is a formatting detail.
"""

import pytest

from app.access import Principal
from app.errors import UnprocessableEntity
from app.services import conversation
from tests.fakes import (
    FakeChatModel,
    says,
    wants,  # noqa: E402
)


def principal():
    return Principal(sub="kc-1", email=None, display_name="P", roles=frozenset({"hr_manager"}))


def run(model, salary, question="What is our total payroll?", **kwargs):
    return list(
        conversation.stream_answer(
            question, model=model, salary=salary, caller=principal(), **kwargs
        )
    )


def tokens(events):
    return "".join(e["text"] for e in events if e["type"] == "token")


def kinds(events):
    return [e["type"] for e in events]


# --- the happy path --------------------------------------------------------------


def test_prose_is_released_once_a_tool_has_grounded_it(salary):
    salary.responses["/dashboard"] = {"total_payroll_usd": "1000000.00"}
    model = FakeChatModel(
        wants(("get_dashboard_summary", {})),
        says("Total payroll is $1,000,000.00."),
    )

    events = run(model, salary)

    assert tokens(events) == "Total payroll is $1,000,000.00."


def test_prose_arrives_in_several_events_rather_than_one(salary):
    """The point of the exercise: the answer appears as it is written.

    Asserted as "more than one event, and together they are the answer" rather than
    on exact chunk boundaries - those belong to the provider, and pinning them here
    would test the fake.
    """
    salary.responses["/dashboard"] = {"total_payroll_usd": "1.00"}
    model = FakeChatModel(
        wants(("get_dashboard_summary", {})),
        says("Total payroll is one dollar across the company."),
    )

    events = run(model, salary)

    pieces = [e["text"] for e in events if e["type"] == "token"]
    assert len(pieces) > 1
    assert "".join(pieces) == "Total payroll is one dollar across the company."


def test_the_user_is_told_which_tool_is_running(salary):
    """A tool round is the slow part. Silence for three seconds reads as broken; a
    line saying what is happening does not."""
    salary.responses["/dashboard"] = {"total_payroll_usd": "1.00"}
    model = FakeChatModel(wants(("get_dashboard_summary", {})), says("done"))

    events = run(model, salary)

    status = [e for e in events if e["type"] == "status"]
    assert status and status[0]["tool"] == "get_dashboard_summary"


def test_the_last_event_carries_the_figures(salary):
    """Same provenance the non-streaming endpoint returns, delivered at the end
    because that is when it is complete."""
    salary.responses["/dashboard"] = {"total_payroll_usd": "1000000.00"}
    model = FakeChatModel(wants(("get_dashboard_summary", {})), says("ok"))

    events = run(model, salary)

    assert kinds(events)[-1] == "done"
    assert events[-1]["used_tools"] == ["get_dashboard_summary"]
    assert events[-1]["figures"][0]["tool"] == "get_dashboard_summary"


# --- the guard, under streaming --------------------------------------------------


def test_an_ungrounded_answer_is_never_emitted_as_tokens(salary):
    """The whole reason prose is held. The model invents a figure on the first turn,
    having called nothing; the user must not see a character of it."""
    model = FakeChatModel(says("Your total payroll is about $4.2 million."))

    events = run(model, salary)

    assert "4.2 million" not in tokens(events)
    assert "4.2 million" not in str(events)


def test_the_refusal_is_sent_instead(salary):
    model = FakeChatModel(says("Your total payroll is about $4.2 million."))

    events = run(model, salary)

    assert "payroll" in tokens(events).lower()
    assert events[-1]["type"] == "done"
    assert events[-1]["used_tools"] == []


def test_an_out_of_scope_question_streams_only_the_refusal(salary):
    model = FakeChatModel(says("Paris."))

    events = run(model, salary, question="What is the capital of France?")

    assert "paris" not in tokens(events).lower()
    assert model.seen == []


def test_a_reference_note_grounds_prose_from_the_first_token(salary):
    """ "How is the median calculated" has no tool. The notes are in the prompt, so
    the answer is grounded before the model says anything and can stream live."""
    model = FakeChatModel(says("The median is the middle salary."))

    events = run(
        model,
        salary,
        question="How is the median salary calculated?",
        context="The median is the middle salary once sorted.",
    )

    assert tokens(events) == "The median is the middle salary."


def test_a_failing_tool_does_not_release_an_invented_figure(salary):
    def explode(path, params, *, caller):
        raise RuntimeError("down")

    salary.get = explode
    model = FakeChatModel(
        wants(("get_dashboard_summary", {})), says("Total payroll is $4.2 million.")
    )

    events = run(model, salary)

    assert "4.2 million" not in tokens(events)
    assert "could not" in tokens(events).lower()


def test_a_tool_not_on_the_menu_is_never_run(salary):
    model = FakeChatModel(wants(("delete_everything", {})), says("I cannot do that."))

    run(model, salary)

    assert salary.calls == []


def test_the_loop_still_stops(salary):
    salary.responses["/dashboard"] = {"total_payroll_usd": "1.00"}
    model = FakeChatModel(*([wants(("get_dashboard_summary", {}))] * 5), says("fine"))

    events = run(model, salary, max_turns=2)

    assert len([e for e in events if e["type"] == "status"]) <= 2
    assert kinds(events)[-1] == "done"


def test_an_empty_question_is_refused_before_anything_streams(salary):
    model = FakeChatModel(says("x"))

    with pytest.raises(UnprocessableEntity):
        run(model, salary, question="  ")

    assert model.seen == []
