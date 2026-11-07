"""Resolving the audit actor.

Not access control - storage. ``audit_log.actor_id`` is a foreign key into
``app_user`` and the only identity a request carries is a Keycloak subject, so
something has to turn one into the other. "Who changed this salary" must still answer
in three years, from the database alone, with Keycloak unreachable and the realm long
since rebuilt.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.access import Principal
from app.models import AppUser
from app.services.user import resolve_actor


def principal(sub: str = "kc-sub-new", email: str | None = "anita@acme.test") -> Principal:
    return Principal(
        sub=sub,
        email=email,
        display_name="Anita Rao",
        roles=frozenset({"hr_manager"}),
    )


def count(db, sub: str) -> int:
    return db.execute(
        text("SELECT count(*) FROM app_user WHERE external_id = :sub"), {"sub": sub}
    ).scalar_one()


def test_first_request_from_a_new_subject_creates_one_app_user_row(db):
    actor = resolve_actor(db, principal())

    assert actor.id is not None
    assert actor.external_id == "kc-sub-new"
    assert count(db, "kc-sub-new") == 1


def test_second_request_from_the_same_subject_creates_no_further_row(db):
    first = resolve_actor(db, principal())
    second = resolve_actor(db, principal())

    assert first.id == second.id
    assert count(db, "kc-sub-new") == 1


def test_the_row_is_keyed_on_the_subject_not_the_email(db):
    """People change email. An audit trail keyed on a mutable field is not one."""
    original = resolve_actor(db, principal(email="anita@acme.test"))
    renamed = resolve_actor(db, principal(email="anita.rao@acme.test"))

    assert renamed.id == original.id
    assert renamed.external_id == "kc-sub-new"


def test_a_changed_email_updates_the_row(db):
    resolve_actor(db, principal(email="anita@acme.test"))
    updated = resolve_actor(db, principal(email="anita.rao@acme.test"))

    assert updated.email == "anita.rao@acme.test"


def test_an_unchanged_principal_issues_no_write(db):
    """The steady state of a request is one SELECT. Writing on every call would turn
    every GET into a write transaction, all of them contending for the same row."""
    resolve_actor(db, principal())
    db.flush()

    before = db.execute(
        text("SELECT xmin FROM app_user WHERE external_id = 'kc-sub-new'")
    ).scalar_one()
    resolve_actor(db, principal())
    db.flush()
    after = db.execute(
        text("SELECT xmin FROM app_user WHERE external_id = 'kc-sub-new'")
    ).scalar_one()

    assert before == after


def test_a_missing_email_is_stored_as_null_not_as_a_string(db):
    """Keycloak does not guarantee an email claim. 'None' in a column is a value that
    looks like data and is not."""
    actor = resolve_actor(db, principal(email=None))
    assert actor.email is None


def test_two_subjects_get_two_rows(db):
    first = resolve_actor(db, principal(sub="kc-sub-a"))
    second = resolve_actor(db, principal(sub="kc-sub-b"))

    assert first.id != second.id


def test_a_lost_race_returns_the_winner_s_row_rather_than_failing(db, monkeypatch):
    """Two first requests from the same new user can arrive together. The unique
    index on external_id decides it; the loser must re-read, not 500.

    The race is forced rather than waited for: _find is made to miss once, so the
    INSERT that follows hits the constraint exactly as the losing request would.
    """
    from app.services import user as service

    existing = resolve_actor(db, principal())
    db.flush()

    real_find = service._find
    calls = {"n": 0}

    def find_missing_once(session, sub):
        calls["n"] += 1
        return None if calls["n"] == 1 else real_find(session, sub)

    monkeypatch.setattr(service, "_find", find_missing_once)

    recovered = resolve_actor(db, principal())

    assert recovered.id == existing.id
    assert count(db, "kc-sub-new") == 1


def test_the_unique_index_is_what_decides_it(db):
    """The guarantee is the database's, not the application's. Without this the race
    recovery above is protecting against something that cannot happen anyway."""
    db.add(AppUser(external_id="kc-dup", email=None, display_name=None))
    db.flush()
    db.add(AppUser(external_id="kc-dup", email=None, display_name=None))

    with pytest.raises(IntegrityError):
        db.flush()
