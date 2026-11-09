"""The tool-calling loop.

The model is mocked throughout. What is being tested is the *harness* - which tool
ran, what came back, when the loop stops, and what the user is told - not whether a
language model is any good, which no test here could establish anyway.

The loop is the security boundary. A system prompt asking the model to behave is a
request; these are the parts that do not depend on it complying.
"""

import pytest

from app.access import Principal
from app.errors import UnprocessableEntity
from app.services import conversation
from app.services.tools import UnknownTool
from tests.fakes import FakeChatModel, says, tool_names, wants


def principal() -> Principal:
    return Principal(
        sub="kc-1", email="hr@acme.test", display_name="Priya", roles=frozenset({"hr_manager"})
    )


def ask(model, salary, question="What is our total payroll?", **kwargs):
    return conversation.answer(question, model=model, salary=salary, caller=principal(), **kwargs)


# --- the happy path --------------------------------------------------------------


def test_a_question_with_no_grounding_gets_the_standard_refusal(salary):
    """Changed deliberately when the grounding guard went in.

    "What can you do?" reaches no tool and matches no note, so whatever the model
    said came from its own training. The refusal is returned instead - and since it
    lists what the assistant *can* answer, it happens to be a better reply to this
    particular question than the model's own.
    """
    model = FakeChatModel(says("I am a helpful assistant and can do anything."))

    result = ask(model, salary, question="What can you do?")

    assert "payroll" in result.text.lower()
    # The model's own words are gone, not merely accompanied by a disclaimer.
    assert "helpful assistant" not in result.text.lower()
    assert result.used_tools == []
    assert salary.calls == []


def test_a_question_about_payroll_calls_the_tool_and_answers(salary):
    salary.responses["/dashboard"] = {"employee_count": 42, "total_payroll_usd": "1000000.00"}
    model = FakeChatModel(
        wants(("get_dashboard_summary", {})),
        says("Total payroll is $1,000,000.00 across 42 employees."),
    )

    result = ask(model, salary)

    assert result.used_tools == ["get_dashboard_summary"]
    assert "1,000,000.00" in result.text


def test_the_tool_result_comes_back_to_the_model(salary):
    """Without this the model is answering from nothing and will invent a number."""
    salary.responses["/dashboard"] = {"employee_count": 42}
    model = FakeChatModel(wants(("get_dashboard_summary", {})), says("42 employees."))

    ask(model, salary)

    second_turn = model.seen[1]
    assert any("42" in str(message.content) for message in second_turn)


def test_the_figures_are_returned_alongside_the_prose(salary):
    """The UI shows what the answer was computed from. A number with no provenance
    is a number nobody can check, and this is a product where being quietly wrong is
    worse than being unavailable."""
    salary.responses["/dashboard"] = {"employee_count": 42}
    model = FakeChatModel(wants(("get_dashboard_summary", {})), says("42 employees."))

    result = ask(model, salary)

    assert result.figures == [{"tool": "get_dashboard_summary", "result": {"employee_count": 42}}]


def test_several_tools_in_one_turn_all_run(salary):
    salary.responses["/reference"] = {"countries": [{"code": "IN", "name": "India"}]}
    model = FakeChatModel(
        wants(("list_countries", {}), ("get_dashboard_summary", {"country": "IN"})),
        says("India has..."),
    )

    result = ask(model, salary)

    assert result.used_tools == ["list_countries", "get_dashboard_summary"]


def test_a_tool_result_can_inform_the_next_tool_call(salary):
    """ "What is the average in Engineering" needs the department id first. The loop
    has to allow a second round, or every name-to-id question fails."""
    salary.responses["/reference"] = {"departments": [{"id": 3, "name": "Engineering"}]}
    model = FakeChatModel(
        wants(("list_departments", {})),
        wants(("get_dashboard_summary", {"department": 3})),
        says("Engineering averages..."),
    )

    result = ask(model, salary)

    assert result.used_tools == ["list_departments", "get_dashboard_summary"]
    assert salary.calls[-1] == ("/dashboard", {"department": 3})


# --- the guarantees --------------------------------------------------------------


def test_a_tool_that_is_not_on_the_menu_is_refused_and_the_model_is_told(salary):
    """Refused, and the conversation continues rather than erroring - the model is
    told its call was rejected and can answer from what it has.

    Here it has nothing, so the grounding guard replaces its reply. The important
    assertions are that the tool never ran and no data was touched.
    """
    model = FakeChatModel(
        wants(("delete_all_employees", {})),
        says("I cannot do that, but I can report payroll figures."),
    )

    result = ask(model, salary)

    assert salary.calls == []
    assert result.used_tools == []
    assert "payroll" in result.text.lower()


def test_the_loop_stops_rather_than_calling_tools_forever(salary):
    """A model that keeps asking for one more tool would otherwise run until the
    request times out, burning quota on every turn."""
    model = FakeChatModel(*[wants(("get_dashboard_summary", {}))] * 20)

    result = ask(model, salary, max_turns=3)

    assert len(salary.calls) <= 3
    assert result.text  # it still says something rather than returning nothing


def test_the_model_is_only_ever_offered_the_tools_this_caller_may_use(salary):
    """Not the whole registry - the caller's slice of it. A model cannot be talked
    into calling something it was never told about."""
    from app.services import tools as tool_module

    model = FakeChatModel(wants(("list_countries", {})), says("Hello."))

    ask(model, salary)

    offered = tool_names(model.offered)
    assert offered == {tool.name for tool in tool_module.permitted(principal())}


def test_the_system_prompt_names_no_tables(salary):
    """No schema in the prompt. A model that knows the table names is a model that
    can be talked into describing them."""
    model = FakeChatModel(says("Hello."))

    ask(model, salary)

    system = model.seen[0][0].content.lower()
    for forbidden in ("select ", "employee_code", "fx_rate", "app_user", "audit_log"):
        assert forbidden not in system


def test_the_question_is_passed_through_unchanged(salary):
    model = FakeChatModel(says("Hello."))

    ask(model, salary, question="How many people work in India?")

    assert model.seen[0][-1].content == "How many people work in India?"


def test_a_failing_tool_does_not_take_the_whole_answer_down(salary):
    """The salary API being briefly unavailable should cost the user one figure, not
    the conversation."""

    def explode(path, params, *, caller):
        raise RuntimeError("upstream is down")

    salary.get = explode
    model = FakeChatModel(
        wants(("get_dashboard_summary", {})),
        says("I could not read the payroll figures just now."),
    )

    result = ask(model, salary)

    assert "could not" in result.text.lower()


def test_an_empty_question_is_refused_before_the_model_is_called(salary):
    """A blank prompt is a wasted call to a rate-limited free tier."""
    model = FakeChatModel(says("should not be reached"))

    with pytest.raises(UnprocessableEntity):
        ask(model, salary, question="   ")

    assert model.seen == []


def test_an_overlong_question_is_refused(salary):
    model = FakeChatModel(says("should not be reached"))

    with pytest.raises(UnprocessableEntity):
        ask(model, salary, question="x" * 5000)

    assert model.seen == []


def test_unknown_tool_is_the_dispatchers_decision_not_the_loops(salary):
    """Belt and braces: the loop catches UnknownTool, but the thing that raises it
    is the registry lookup. If that ever stopped raising, this test fails rather
    than the whitelist quietly becoming advisory."""
    from app.services import tools as tool_module

    with pytest.raises(UnknownTool):
        tool_module.run("nope", {}, salary=salary, caller=principal())


def test_the_tool_result_is_sent_back_as_a_tool_message(salary):
    """LangChain owns the shape a tool result takes on the wire.

    This used to assert that `arguments` was echoed as a JSON string rather than a
    dict - a provider detail that cost an afternoon when it was hand-rolled, and is
    now somebody else's problem. What is still ours is that the result reaches the
    model at all, attached to the call it answers.
    """
    from langchain_core.messages import ToolMessage

    salary.responses["/dashboard"] = {"employee_count": 42}
    model = FakeChatModel(wants(("get_dashboard_summary", {"country": "IN"})), says("42."))

    ask(model, salary)

    second_turn = model.seen[1]
    results = [m for m in second_turn if isinstance(m, ToolMessage)]
    assert len(results) == 1
    assert "42" in results[0].content
    assert results[0].tool_call_id
