"""Which tools a person may use.

Until now every tool was offered to everyone, and the only thing standing between an
HR Manager and an admin-only figure was the salary API refusing the call. That is a
real backstop and it is not enough on its own: the model would still choose the
tool, call it, be refused, and then have to explain a 403 it cannot interpret - and
the user would be told something had gone wrong rather than that they are not
allowed.

So access is decided twice, deliberately:

  * **Before the model sees the menu.** A tool the caller may not use is not in the
    list sent to the model, so it cannot be chosen. A model cannot be talked into
    calling a tool it was never told about.
  * **At dispatch.** Checked again when the call comes back, because the first check
    is a filter on a list and the second is a guard on an action. A bug that let a
    stale tool name through would otherwise execute.

The salary API's own role check remains underneath both. Three layers, each of which
would hold alone.
"""

import pytest

from app.access import Principal, Role
from app.services import tools


def principal(*roles: str) -> Principal:
    return Principal(
        sub="kc-1", email=None, display_name="P", roles=frozenset(roles or ("hr_manager",))
    )


HR = principal("hr_manager")
ADMIN = principal("admin")
NOBODY = principal("")


# --- the menu is filtered -------------------------------------------------------


def test_an_hr_manager_is_offered_the_tools_they_can_use():
    offered = {tool["name"] for tool in tools.schemas(caller=HR)}

    assert "get_dashboard_summary" in offered
    assert "count_employees" in offered


def test_a_caller_with_no_role_is_offered_nothing():
    """Not an empty answer from a full menu - an empty menu. There is nothing for a
    roleless caller to call."""
    assert tools.schemas(caller=NOBODY) == []


def test_every_tool_declares_who_may_use_it():
    """A tool with no declared roles would be open to anyone by omission, which is
    the kind of default that is only noticed after it matters."""
    for tool in tools.REGISTRY.values():
        assert tool.roles, tool.name


def test_the_admin_menu_is_a_superset_of_the_hr_menu():
    """Nothing is hidden from an admin that an HR Manager can see. If that ever
    stops being true it should be a deliberate decision, not an accident."""
    hr = {tool["name"] for tool in tools.schemas(caller=HR)}
    admin = {tool["name"] for tool in tools.schemas(caller=ADMIN)}

    assert hr <= admin


# --- and dispatch checks again --------------------------------------------------


def test_a_tool_the_caller_may_not_use_is_refused_at_dispatch(salary):
    """The second layer. The model cannot see this tool, so reaching here means
    something else went wrong - and it still must not run."""
    assert "get_dashboard_summary" in tools.REGISTRY  # a real tool, not a typo

    with pytest.raises(tools.NotPermitted):
        tools.run("get_dashboard_summary", {}, salary=salary, caller=NOBODY)


def test_a_refused_tool_touches_no_data(salary):
    with pytest.raises(tools.NotPermitted):
        tools.run("count_employees", {}, salary=salary, caller=NOBODY)

    assert salary.calls == []


def test_a_permitted_tool_still_runs(salary):
    tools.run("get_dashboard_summary", {}, salary=salary, caller=HR)

    assert salary.calls == [("/dashboard", {})]


def test_an_unknown_tool_is_still_unknown_rather_than_forbidden(salary):
    """The two refusals are different and the distinction is useful in a log:
    "no such tool" is a model hallucinating, "not permitted" is an access problem."""
    with pytest.raises(tools.UnknownTool):
        tools.run("delete_everything", {}, salary=salary, caller=ADMIN)


def test_roles_are_declared_with_the_enum_not_as_loose_strings():
    """A typo in a role string fails open - the role never matches, so nobody gets
    the tool, which looks like a permissions bug rather than a typo. The enum makes
    the typo a NameError at import."""
    for tool in tools.REGISTRY.values():
        for role in tool.roles:
            assert isinstance(role, Role), f"{tool.name} declares a bare string"
