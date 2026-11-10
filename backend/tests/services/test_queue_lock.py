"""One file at a time, however many containers are running.

`FOR UPDATE SKIP LOCKED` stops two workers taking the *same* job. It does nothing to
stop them taking two *different* jobs and applying both at once - so with two API
replicas, two imports would run simultaneously, and each one's "is this employee
code already taken?" would be answered against a database the other was halfway
through changing.

Until this lock existed, "one file at a time" was true only because the stack
happened to run a single container. `docker compose up --scale api=2` would have
removed the guarantee silently, which is the worst way to lose one.

These tests use two real sessions on two real connections. A single session would
prove nothing: Postgres advisory locks are re-entrant within a session, so one
session can take the same lock twice and the test would pass while the property
failed.
"""

import pytest
from sqlalchemy.orm import Session

from app.services import importer


@pytest.fixture
def other(engine):
    """A second connection, standing in for a second API replica."""
    connection = engine.connect()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        connection.close()


def test_one_worker_gets_the_lock(db):
    assert importer.acquire_queue_lock(db) is True
    importer.release_queue_lock(db)


def test_a_second_worker_is_refused_while_the_first_holds_it(db, other):
    """The whole point. Two replicas, one importer."""
    assert importer.acquire_queue_lock(db) is True

    assert importer.acquire_queue_lock(other) is False

    importer.release_queue_lock(db)


def test_the_second_worker_gets_it_once_the_first_is_done(db, other):
    importer.acquire_queue_lock(db)
    importer.release_queue_lock(db)

    assert importer.acquire_queue_lock(other) is True

    importer.release_queue_lock(other)


def test_the_lock_survives_a_commit(db, other):
    """Load-bearing, and the reason this is a session lock and not a transaction one.

    `run_one` commits after claiming so PROCESSING is visible while the file is still
    being applied. A transaction-scoped lock would be released by exactly that commit
    - handing the queue to the next worker halfway through an import.
    """
    importer.acquire_queue_lock(db)
    db.commit()

    assert importer.acquire_queue_lock(other) is False

    importer.release_queue_lock(db)


def test_releasing_a_lock_nobody_holds_is_not_an_error(db):
    """Called from a finally block, so it runs on paths that never took the lock."""
    importer.release_queue_lock(db)


def test_a_dropped_connection_releases_the_lock(engine, other):
    """A container that dies must not leave the queue locked forever.

    This is why the lock is held by the connection rather than by a row with a lease:
    there is nothing to expire and nothing to clean up. Postgres releases it when the
    connection goes.

    `invalidate()`, not `close()`, and the difference matters. `close()` returns the
    connection to SQLAlchemy's pool - the socket stays open, so Postgres still sees
    the session and still holds the lock. Only the underlying connection actually
    going away releases it, which is what happens when a container dies.
    """
    doomed = engine.connect()
    session = Session(bind=doomed)
    assert importer.acquire_queue_lock(session) is True
    doomed.invalidate()  # the container dies here

    assert importer.acquire_queue_lock(other) is True
    importer.release_queue_lock(other)


def test_the_worker_does_not_even_look_for_work_without_the_lock(other, monkeypatch):
    """The lock has to be checked *before* claiming, not after.

    Without this the mutation passes unnoticed: deleting the check from `run_one`
    breaks none of the tests above, because they exercise the lock directly rather
    than the worker that depends on it. Here `claim` is replaced with something that
    raises, so reaching it at all is the failure.
    """
    from app import worker

    def must_not_be_called(_db):
        raise AssertionError("claimed work while another worker held the queue lock")

    monkeypatch.setattr(importer, "claim", must_not_be_called)
    assert importer.acquire_queue_lock(other) is True

    try:
        assert worker.run_one() is False
    finally:
        importer.release_queue_lock(other)
