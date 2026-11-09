"""The tools the model is allowed to call, and nothing else.

A chatbot over payroll is an access-control problem wearing a conversational hat.
Two things make it safe, and both are tested here:

  * **The tool list is a whitelist, not a suggestion.** The model proposes a name;
    this service looks it up in a dict. A name that is not in the dict is refused
    before anything runs, so a model that hallucinates `delete_employee` or is
    talked into it by a crafted question gets an error, not an effect.
  * **Every tool runs as the person asking.** Tools call the salary API over HTTP
    with that user's own claims headers, so the salary service applies the same role
    check it applies to the UI. The bot cannot read what the user could not read by
    clicking.

No tool writes. Every one is a GET.
"""

import pytest

from app.access import Principal
from app.services import tools


class FakeSalary:
    """Records what was asked of the salary API, and answers with canned data."""

    def __init__(self, responses: dict | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.responses = responses or {}

    def get(self, path: str, params: dict, *, caller: Principal) -> dict:
        self.calls.append((path, params))
        self.caller = caller
        return self.responses.get(path, {})


def principal(roles=("hr_manager",)) -> Principal:
    return Principal(sub="kc-1", email="hr@acme.test", display_name="Priya", roles=frozenset(roles))


# --- the whitelist ---------------------------------------------------------------


def test_every_declared_tool_can_actually_be_run():
    """A schema the model can see but the dispatcher cannot run is a guaranteed
    dead end mid-conversation."""
    declared = {tool["name"] for tool in tools.schemas(principal())}

    assert declared == set(tools.REGISTRY)


def test_an_undeclared_tool_is_refused(salary):
    with pytest.raises(tools.UnknownTool):
        tools.run("delete_everything", {}, salary=salary, caller=principal())


def test_refusing_an_unknown_tool_calls_nothing(salary):
    """The refusal has to happen before dispatch, not inside a handler that was
    reached by mistake."""
    with pytest.raises(tools.UnknownTool):
        tools.run("drop_table", {"table": "salary"}, salary=salary, caller=principal())

    assert salary.calls == []


def test_no_tool_writes():
    """Read-only by construction. A write tool would make a sentence typed into a
    chat box a mutation with no form, no confirmation and no second pair of eyes."""
    assert all(spec.method == "GET" for spec in tools.REGISTRY.values())


def test_every_tool_is_documented_for_the_model():
    """The description is the only thing the model has to choose with. A blank one
    makes tool choice a coin toss."""
    for tool in tools.schemas(principal()):
        assert len(tool["description"]) > 30, tool["name"]


# --- running as the asking user --------------------------------------------------


def test_a_tool_calls_the_salary_api_as_the_person_asking(salary):
    caller = principal()

    tools.run("get_dashboard_summary", {}, salary=salary, caller=caller)

    assert salary.caller is caller


def test_the_dashboard_tool_hits_the_dashboard_endpoint(salary):
    tools.run("get_dashboard_summary", {}, salary=salary, caller=principal())

    assert salary.calls == [("/dashboard", {})]


def test_filters_are_passed_through(salary):
    tools.run(
        "get_dashboard_summary",
        {"country": "IN", "department": 3},
        salary=salary,
        caller=principal(),
    )

    assert salary.calls == [("/dashboard", {"country": "IN", "department": 3})]


def test_an_argument_the_tool_does_not_declare_is_dropped(salary):
    """A model will invent a plausible argument. Forwarding it unchecked would let a
    question add query parameters the endpoint never meant to expose."""
    tools.run(
        "get_dashboard_summary",
        {"country": "IN", "limit": 999, "order_by": "salary"},
        salary=salary,
        caller=principal(),
    )

    assert salary.calls == [("/dashboard", {"country": "IN"})]


def test_a_null_filter_is_dropped_rather_than_sent_as_the_string_none(salary):
    """Models emit nulls for optional arguments constantly. `country=None` on the
    wire filters to a country called "None" and returns an empty dashboard, which
    reads as "ACME has no employees"."""
    tools.run("get_dashboard_summary", {"country": None}, salary=salary, caller=principal())

    assert salary.calls == [("/dashboard", {})]


# --- the individual tools --------------------------------------------------------


def test_count_employees_asks_for_one_row_not_the_whole_list(salary):
    """It needs the total, not the people. Pulling 10,000 rows to count them would
    be slow and would put every name in the model's context."""
    tools.run("count_employees", {"country": "IN"}, salary=salary, caller=principal())

    path, params = salary.calls[0]
    assert path == "/employees"
    assert params["size"] == 1
    assert params["country"] == "IN"


def test_count_employees_returns_only_the_total(salary):
    salary.responses["/employees"] = {
        "total": 42,
        "items": [{"first_name": "Priya", "last_name": "Sharma"}],
    }

    result = tools.run("count_employees", {}, salary=salary, caller=principal())

    assert result == {"employee_count": 42}
    assert "items" not in result


def test_list_departments_returns_names_and_ids_only(salary):
    """The model needs the id to filter with and the name to say back. The rest of
    the reference payload is noise in a context window."""
    salary.responses["/reference"] = {
        "departments": [{"id": 1, "name": "Engineering", "is_active": True}],
        "countries": [{"code": "IN", "name": "India"}],
        "currencies": [],
        "bands": [],
    }

    result = tools.run("list_departments", {}, salary=salary, caller=principal())

    assert result == {"departments": [{"id": 1, "name": "Engineering"}]}


def test_list_countries_returns_codes_and_names(salary):
    salary.responses["/reference"] = {
        "countries": [{"code": "IN", "name": "India"}],
        "departments": [],
        "currencies": [],
        "bands": [],
    }

    result = tools.run("list_countries", {}, salary=salary, caller=principal())

    assert result == {"countries": [{"code": "IN", "name": "India"}]}


def test_top_paid_caps_how_many_people_it_will_name(salary):
    """ "Show me the top 500 earners" is a payroll dump phrased as a question."""
    tools.run("top_paid", {"n": 500}, salary=salary, caller=principal())

    _, params = salary.calls[0]
    assert params["size"] <= tools.MAX_PEOPLE


def test_top_paid_sorts_by_pay_descending(salary):
    """The sort key is the API's column name, not the word a human would use.

    This was wrong first time round - "salary" - and every top_paid question failed
    with a 422 from the allow-list. A fake cannot catch that, which is why the
    service is also exercised against the real API before it ships.
    """
    tools.run("top_paid", {"n": 3}, salary=salary, caller=principal())

    _, params = salary.calls[0]
    assert params["sort"] == tools.SALARY_SORT == "usd_amount"
    assert params["order"] == "desc"


def test_lowest_paid_sorts_the_other_way(salary):
    tools.run("lowest_paid", {"n": 3}, salary=salary, caller=principal())

    _, params = salary.calls[0]
    assert params["sort"] == tools.SALARY_SORT
    assert params["order"] == "asc"


def test_top_paid_reads_the_list_endpoint_s_nested_shape(salary):
    """The list returns {"employee": {...}, "current_salary": {...}}, not a flat row.

    This test exists because the fake used to return a flat shape nobody had
    checked against the real API, so the tool silently produced three people with
    no name and no salary - and the model, handed that, said it had no data. The
    evaluation found it; this pins it.
    """
    salary.responses["/employees"] = {
        "total": 1,
        "items": [
            {
                "employee": {
                    "first_name": "Noah",
                    "last_name": "Muller",
                    "country_code": "DE",
                    "email": "noah@acme.test",
                },
                "current_salary": {"usd_amount": "425495.00", "currency": "EUR"},
            }
        ],
    }

    result = tools.run("top_paid", {"n": 1}, salary=salary, caller=principal())

    assert result["people"] == [{"name": "Noah Muller", "country": "DE", "salary_usd": "425495.00"}]


def test_top_paid_returns_pay_without_contact_details(salary):
    """A salary figure against a name is the point of the question. An email address
    is not, and it is the field that turns an answer into a mailing list."""
    salary.responses["/employees"] = {
        "total": 1,
        "items": [
            {
                "employee": {
                    "id": 1,
                    "employee_code": "EMP-1",
                    "first_name": "Priya",
                    "last_name": "Sharma",
                    "email": "priya@acme.test",
                    "country_code": "IN",
                },
                "current_salary": {"usd_amount": "120000.00"},
            }
        ],
    }

    result = tools.run("top_paid", {"n": 1}, salary=salary, caller=principal())

    person = result["people"][0]
    assert person["name"] == "Priya Sharma"
    assert person["salary_usd"] == "120000.00"
    assert "email" not in person


def test_optional_filters_accept_null():
    """Models emit `null` for optional arguments constantly, and the provider
    validates the call against the schema *before* it reaches us - so a field that
    forbids null makes every such call a 400 and loses the conversation.

    Found by running the real model: every tool-calling question failed with
    `tool_use_failed: expected string, but got null`. Declaring the fields as
    `str | None` is what makes LangChain render them nullable.
    """
    for tool in tools.REGISTRY.values():
        for name, field in tool.args.model_fields.items():
            assert not field.is_required(), f"{tool.name}.{name} has no default"


def test_optional_fields_still_declare_their_real_type():
    """Nullable, not untyped. The model is still told a country code is a string;
    dropping to "any" would lose that."""
    schema = tools.CountArgs.model_json_schema()

    country = schema["properties"]["country"]["anyOf"]
    assert {"type": "string"} in country
    assert {"type": "null"} in country


def test_count_employees_counts_active_people_by_default(salary):
    """ "How many people do we employ" means people on the payroll now.

    Every other figure in the product - headcount, totals, averages, the median -
    counts active employees with an open salary row. Counting leavers here made the
    assistant report 6,092 for India while the dashboard said 5,940, which is the
    worst kind of wrong: both numbers are real, and nothing says which question was
    answered.
    """
    tools.run("count_employees", {"country": "IN"}, salary=salary, caller=principal())

    _, params = salary.calls[0]
    assert params["status"] == "ACTIVE"


def test_a_question_about_leavers_can_still_override_the_default(salary):
    """The default is a default, not a cage - "how many have left" is a real
    question and the model can ask it."""
    tools.run("count_employees", {"status": "INACTIVE"}, salary=salary, caller=principal())

    _, params = salary.calls[0]
    assert params["status"] == "INACTIVE"
