"""What the model is allowed to do, and who is allowed to ask for it.

A chatbot over payroll is an access-control problem wearing a conversational hat.
Four rules hold it together, and they are structural rather than prompted - a system
prompt is a request, and a model under a determined question will grant it.

  1. **A whitelist, not a suggestion.** The model proposes a tool name; this module
     looks it up in `REGISTRY`. Anything else is refused before dispatch. There is
     no path from a generated string to a function that is not in that dict.
  2. **Filtered by role, twice.** The menu sent to the model contains only tools the
     caller may use - a model cannot be talked into calling something it was never
     told about - and dispatch checks again, because a filter on a list and a guard
     on an action are different things.
  3. **No tool writes.** Every one is a GET. A write tool would make a sentence
     typed into a chat box a mutation: no form, no confirmation, no second pair of
     eyes, and an audit row whose author is a language model.
  4. **Every call runs as the person asking.** Tools go through the salary API with
     that user's claims headers, so the salary service applies the same role check
     it applies to the UI. That is the third layer, and it holds even if both of
     the above were bypassed.

Argument schemas are Pydantic models handed to LangChain, which converts them to
whatever shape the provider wants. That conversion is where three bugs lived when it
was hand-rolled: optional arguments that forbade `null`, arguments echoed back as a
dict instead of a JSON string, and tool-call fragments split across stream chunks.

No raw SQL is generated and no table names are in the prompt. The model picks from a
menu; it does not write queries.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.access import Principal, Role

log = logging.getLogger("chat.tools")

# The most people any tool will name in one answer. "Show me the top 500 earners" is
# a payroll dump phrased as a question; this is the line between answering and
# exporting.
MAX_PEOPLE = 10

# The salary API sorts on an allow-list and 422s anything else. This is the column
# name in that list - not "salary", which is what it is called everywhere a human
# reads it.
SALARY_SORT = "usd_amount"

# Everyone who may use the assistant at all. Named rather than repeated, so "which
# tools are restricted" stays a question with one place to look.
EVERYONE = frozenset({Role.ADMIN, Role.HR_MANAGER})


class UnknownTool(Exception):
    """The model asked for something that is not on the menu."""


class NotPermitted(Exception):
    """A real tool, and not one this caller may use.

    Distinct from UnknownTool because the two mean different things in a log: one is
    a model hallucinating a capability, the other is an access problem.
    """


# --- argument schemas ------------------------------------------------------------
#
# Optional fields are `| None` with a default, which LangChain renders as nullable.
# Models emit `null` for arguments they are not using, and a schema that forbids it
# makes the provider reject the whole call - a 400 that loses the conversation.


class Filters(BaseModel):
    """The three dimensions every aggregate can be narrowed by."""

    country: str | None = Field(
        default=None,
        description="ISO alpha-2 country code, e.g. IN. Use list_countries first.",
    )
    department: int | None = Field(
        default=None,
        description="Department id. Use list_departments to resolve a name.",
    )
    band: int | None = Field(default=None, description="Band id. Use list_bands first.")


class CountArgs(Filters):
    status: str | None = Field(
        default=None,
        description="ACTIVE (the default) or INACTIVE for people who have left.",
    )


class PeopleArgs(Filters):
    n: int | None = Field(default=5, description=f"How many to return, 1 to {MAX_PEOPLE}.")


class NoArgs(BaseModel):
    """Takes nothing. Declared rather than omitted so every tool has a schema."""


# --- handlers --------------------------------------------------------------------
#
# Each returns the smallest useful shape. Trimming matters twice over: a context
# window is finite, and whatever is returned here is what the model can repeat back.


def _dashboard(args: dict, *, salary, caller: Principal) -> dict:
    return salary.get("/dashboard", args, caller=caller)


def _count(args: dict, *, salary, caller: Principal) -> dict:
    # Active unless asked otherwise. Every other figure in the product counts people
    # on the payroll now, and a count that silently included leavers reported 6,092
    # for India against the dashboard's 5,940 - both real, which is what made it
    # dangerous: nothing said which question had been answered.
    filters = {"status": "ACTIVE", **args}
    # size=1 because the question is "how many", not "who". Fetching the people to
    # count them would be slow and would put every name in the model's context.
    page = salary.get("/employees", {**filters, "size": 1, "page": 1}, caller=caller)
    return {"employee_count": page.get("total", 0)}


def _ranked(args: dict, *, salary, caller: Principal, order: str) -> dict:
    requested = args.pop("n", 5)
    size = max(1, min(int(requested or 5), MAX_PEOPLE))
    page = salary.get(
        "/employees",
        {**args, "size": size, "page": 1, "sort": SALARY_SORT, "order": order},
        caller=caller,
    )
    return {"people": [_person(row) for row in page.get("items", [])]}


def _top_paid(args: dict, *, salary, caller: Principal) -> dict:
    return _ranked(args, salary=salary, caller=caller, order="desc")


def _lowest_paid(args: dict, *, salary, caller: Principal) -> dict:
    return _ranked(args, salary=salary, caller=caller, order="asc")


def _person(row: dict) -> dict:
    """Name and pay, from the list endpoint's nested shape.

    A row is `{"employee": {...}, "current_salary": {...}}`, not a flat record. Read
    flat, every field came back None and the model - correctly - reported that it
    had no data, having been handed empty people.

    Name and pay only. Contact details are what turn an answer into a mailing list.
    """
    employee = row.get("employee") or row
    pay = row.get("current_salary") or {}
    return {
        "name": f"{employee.get('first_name', '')} {employee.get('last_name', '')}".strip(),
        "country": employee.get("country_code"),
        "salary_usd": pay.get("usd_amount"),
    }


def _countries(args: dict, *, salary, caller: Principal) -> dict:
    reference = salary.get("/reference", {}, caller=caller)
    return {
        "countries": [
            {"code": row["code"], "name": row["name"]} for row in reference.get("countries", [])
        ]
    }


def _departments(args: dict, *, salary, caller: Principal) -> dict:
    reference = salary.get("/reference", {}, caller=caller)
    return {
        "departments": [
            {"id": row["id"], "name": row["name"]} for row in reference.get("departments", [])
        ]
    }


def _bands(args: dict, *, salary, caller: Principal) -> dict:
    reference = salary.get("/reference", {}, caller=caller)
    return {
        "bands": [
            {"id": row["id"], "name": row["name"], "rank": row["rank"]}
            for row in reference.get("bands", [])
        ]
    }


# --- the menu --------------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    """One callable the model can see, and who may see it."""

    name: str
    description: str
    args: type[BaseModel]
    handler: Callable[..., dict]
    # Who may use it. Declared with the Role enum rather than bare strings: a typo
    # in a string fails in the most confusing direction - the role never matches,
    # nobody gets the tool, and it reads as a permissions bug rather than a typo.
    roles: frozenset[Role] = EVERYONE
    # Declared and asserted by the tests. A tool that is not a GET does not belong
    # in a chatbot.
    method: str = "GET"


REGISTRY: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        Tool(
            name="get_dashboard_summary",
            description=(
                "Aggregate payroll figures: headcount, total payroll in USD, mean and "
                "median salary, highest and lowest paid, and breakdowns by country, "
                "department, band and currency. Optionally filtered. Use this for any "
                "question about totals, averages or distribution."
            ),
            args=Filters,
            handler=_dashboard,
        ),
        Tool(
            name="count_employees",
            description=(
                "How many employees match a filter. Counts people currently on the "
                "payroll; pass status=INACTIVE to count people who have left. Returns "
                "a count only, never the people."
            ),
            args=CountArgs,
            handler=_count,
        ),
        Tool(
            name="top_paid",
            description=(
                f"The highest-paid employees, at most {MAX_PEOPLE}, with name, country "
                "and salary in USD. Optionally filtered."
            ),
            args=PeopleArgs,
            handler=_top_paid,
        ),
        Tool(
            name="lowest_paid",
            description=(
                f"The lowest-paid employees, at most {MAX_PEOPLE}, with name, country "
                "and salary in USD. Useful for pay-floor and equity questions."
            ),
            args=PeopleArgs,
            handler=_lowest_paid,
        ),
        Tool(
            name="list_countries",
            description=(
                "Every country ACME employs in, as code and name. Call this to turn a "
                "country name in a question into the code the other tools need."
            ),
            args=NoArgs,
            handler=_countries,
        ),
        Tool(
            name="list_departments",
            description=(
                "Every department, as id and name. Call this to turn a department name "
                "into the id the other tools need."
            ),
            args=NoArgs,
            handler=_departments,
        ),
        Tool(
            name="list_bands",
            description=(
                "Every salary band, as id, name and rank. Rank orders them: 1 is most "
                "junior. Bands sort by rank, never by name."
            ),
            args=NoArgs,
            handler=_bands,
        ),
    )
}


def _may_use(caller: Principal, tool: Tool) -> bool:
    return bool(caller.roles & {str(role) for role in tool.roles})


def permitted(caller: Principal) -> list[Tool]:
    """The tools this caller may use. The menu, before the model sees it."""
    return [tool for tool in REGISTRY.values() if _may_use(caller, tool)]


def as_langchain(caller: Principal, *, salary) -> list[StructuredTool]:
    """The permitted tools, bound to this caller, as LangChain tools.

    The caller and the salary client are closed over rather than taken as arguments,
    so neither is something the model can influence: it chooses a tool and its
    filters, never who the call runs as.
    """

    def bind(tool: Tool) -> StructuredTool:
        def call(**kwargs) -> dict:
            return run(tool.name, kwargs, salary=salary, caller=caller)

        return StructuredTool.from_function(
            func=call,
            name=tool.name,
            description=tool.description,
            args_schema=tool.args,
        )

    return [bind(tool) for tool in permitted(caller)]


def schemas(caller: Principal) -> list[dict]:
    """The menu as plain dicts - what the tests and the evaluation assert on."""
    return [{"name": tool.name, "description": tool.description} for tool in permitted(caller)]


def run(name: str, arguments: dict | None, *, salary, caller: Principal) -> dict:
    """Dispatch one tool call.

    Both refusals happen before anything runs, which is the whole guarantee, so they
    are the first two things that happen.
    """
    tool = REGISTRY.get(name)
    if tool is None:
        log.warning("unknown_tool", extra={"tool": name, "sub": caller.sub})
        raise UnknownTool(name)

    if not _may_use(caller, tool):
        # The model was never offered this tool, so arriving here means something
        # else went wrong - and it still must not run.
        log.warning("tool_not_permitted", extra={"tool": name, "sub": caller.sub})
        raise NotPermitted(name)

    filtered = _accepted(tool, arguments or {})
    log.info("tool_call", extra={"tool": name, "tool_args": filtered, "sub": caller.sub})
    return tool.handler(filtered, salary=salary, caller=caller)


def _accepted(tool: Tool, arguments: dict) -> dict:
    """Keep the arguments this tool declares, drop everything else.

    LangChain validates against the schema before calling, so this is belt and
    braces - but `None` still arrives for optional fields the model declined to
    use, and `country=None` on the wire filters to a country named "None" and
    returns an empty dashboard, which reads as "ACME has no employees".
    """
    declared = set(tool.args.model_fields)
    return {
        key: value
        for key, value in arguments.items()
        if key in declared and value is not None and value != ""
    }
