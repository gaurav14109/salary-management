"""Path traversal in the proxy (OWASP A10).

A proxy that forwards a client-supplied path unchecked lets the client choose which
URL the server requests, which is the whole of server-side request forgery.
"""

import pytest

from app.errors import UnsafePath
from app.security.paths import safe_upstream_path


@pytest.mark.parametrize(
    "hostile",
    [
        "../admin",
        "employees/../../admin",
        "..%2fadmin",  # single-encoded
        "..%252fadmin",  # double-encoded
        "%2e%2e/admin",
        "%252e%252e/admin",
        "/absolute/path",  # would produce a double slash upstream
        "employees//../admin",
        "http://evil.test/",  # retarget the proxy entirely
        "employees\nX-Injected: 1",  # header injection
        "employees\r\nHost: evil.test",
    ],
)
def test_a_hostile_path_is_refused(hostile):
    with pytest.raises(UnsafePath):
        safe_upstream_path(hostile)


@pytest.mark.parametrize(
    "benign",
    ["employees", "employees/42", "employees/42/salary", "dashboard", "imports/7", ""],
)
def test_an_ordinary_path_passes(benign):
    assert safe_upstream_path(benign) == benign
