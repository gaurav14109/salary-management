"""The session cookie is the security boundary, so these are its tests."""

import time

from app.config import get_settings
from app.security.session import decode, encode


def test_a_session_round_trips(session):
    assert decode(encode(session)) == session


def test_the_cookie_reveals_nothing(session):
    """If the token were readable, httpOnly would be the only thing protecting it, and
    anything that leaks a cookie value would leak a credential."""
    encoded = encode(session)

    assert session.access_token not in encoded
    assert session.refresh_token not in encoded
    assert "hr@acme.test" not in encoded
    assert "kc-sub-123" not in encoded


def test_a_tampered_cookie_is_not_a_session(session):
    """Fernet authenticates as well as encrypts, so an edited cookie fails to decode
    rather than decoding into something an attacker chose."""
    assert decode(encode(session)[:-6] + "AAAAAA") is None


def test_nonsense_decodes_to_no_session():
    assert decode("not-a-cookie") is None
    assert decode(None) is None
    assert decode("") is None


def test_an_access_token_is_treated_as_expired_slightly_early(session):
    """A token with five seconds left will have lapsed by the time the upstream reads
    it, so it is refreshed before use rather than after a failure."""
    session.expires_at = time.time() + 5

    assert session.access_token_expired() is True


def test_a_session_past_its_absolute_lifetime_is_refused(session):
    """Refreshing must not extend a session forever: without a ceiling a stolen cookie
    is valid indefinitely, and only re-authentication resets it."""
    session.created_at = time.time() - get_settings().session_max_lifetime_seconds - 1

    assert decode(encode(session)) is None
