"""Open redirect (OWASP A01).

A link to a real, trusted domain that lands the user on an attacker's site after a
genuine login is one of the most useful things a phisher can be handed.
"""

import pytest

from app.security.redirects import safe_return_path


@pytest.mark.parametrize(
    "hostile",
    [
        "https://evil.test/",  # absolute URL
        "http://evil.test/path",
        "//evil.test",  # scheme-relative; urljoin treats it as absolute
        "///evil.test",
        "/\\evil.test",  # some browsers normalise backslashes to //
        "\\\\evil.test",
        "javascript:alert(1)",
        "data:text/html,<script>",
        "/dashboard\r\nSet-Cookie: a=b",  # header injection
        "/dashboard\nX-Injected: 1",
    ],
)
def test_a_hostile_return_to_is_reduced_to_the_root(hostile):
    assert safe_return_path(hostile) == "/"


@pytest.mark.parametrize(
    "benign,expected",
    [
        ("/", "/"),
        ("/dashboard", "/dashboard"),
        ("/employees/42", "/employees/42"),
        ("/employees?country=IN&page=2", "/employees?country=IN&page=2"),
        ("/employees#salary", "/employees#salary"),
        (None, "/"),
        ("", "/"),
    ],
)
def test_a_same_site_path_survives(benign, expected):
    assert safe_return_path(benign) == expected
