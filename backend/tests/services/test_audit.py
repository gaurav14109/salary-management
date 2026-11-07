"""The audit trail writer.

Answers "who changed this, when, and from what" - a compliance fact, deliberately
separate from salary history, which is a business fact that feeds every report.

The single rule this file exists to pin down: the audit row lives or dies with the
mutation it describes. Not written afterwards, not written to a different session,
not written by a background task. If the change commits, the record commits; if the
change rolls back, so does the record.
"""

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.audit import Action, EntityType
from app.services.audit import record

# --- what it records ------------------------------------------------------------


def test_record_persists_old_and_new_values(db, actor_id):
    entry = record(
        db,
        entity_type=EntityType.SALARY,
        entity_id=42,
        action=Action.UPDATE,
        old={"amount": "1200000.000"},
        new={"amount": "1400000.000"},
        actor_id=actor_id,
    )

    assert entry.id is not None
    assert entry.entity_type == "SALARY"
    assert entry.action == "UPDATE"
    assert entry.old_value == {"amount": "1200000.000"}
    assert entry.new_value == {"amount": "1400000.000"}
    assert entry.actor_id == actor_id


def test_a_creation_has_no_old_value(db, actor_id):
    entry = record(
        db,
        entity_type=EntityType.EMPLOYEE,
        entity_id=1,
        action=Action.CREATE,
        old=None,
        new={"employee_code": "EMP-1"},
        actor_id=actor_id,
    )

    assert entry.old_value is None
    assert entry.new_value == {"employee_code": "EMP-1"}


def test_created_at_is_set_by_the_database_not_the_caller(db, actor_id):
    """A timestamp the application supplies is a timestamp the application can get
    wrong - or lie about. now() is the database's clock, shared by every writer."""
    entry = record(
        db,
        entity_type=EntityType.FX_RATE,
        entity_id=7,
        action=Action.CREATE,
        old=None,
        new={"units_per_usd": "83.45000000"},
        actor_id=actor_id,
    )
    db.flush()

    assert isinstance(entry.created_at, datetime)
    assert entry.created_at.tzinfo is not None


# --- precision: the reason this is not json.dumps(obj) --------------------------


def test_decimal_values_serialize_as_strings(db, actor_id):
    """JSONB numbers go through IEEE 754 doubles. A salary round-tripped through one
    is no longer the salary that was stored, and the audit trail would then disagree
    with the row it is supposed to prove."""
    entry = record(
        db,
        entity_type=EntityType.SALARY,
        entity_id=1,
        action=Action.CREATE,
        old=None,
        new={"amount": Decimal("12345.678"), "usd_amount": Decimal("40234.56")},
        actor_id=actor_id,
    )
    db.flush()
    db.expire(entry)

    assert entry.new_value == {"amount": "12345.678", "usd_amount": "40234.56"}


def test_a_three_decimal_amount_keeps_its_last_digit(db, actor_id):
    entry = record(
        db,
        entity_type=EntityType.SALARY,
        entity_id=1,
        action=Action.UPDATE,
        old={"amount": Decimal("0.001")},
        new={"amount": Decimal("12345.678")},
        actor_id=actor_id,
    )
    db.flush()
    db.expire(entry)

    assert entry.old_value["amount"] == "0.001"
    assert entry.new_value["amount"] == "12345.678"


def test_dates_serialize_as_iso_strings(db, actor_id):
    entry = record(
        db,
        entity_type=EntityType.SALARY,
        entity_id=1,
        action=Action.CREATE,
        old=None,
        new={"effective_from": date(2026, 4, 1)},
        actor_id=actor_id,
    )
    db.flush()
    db.expire(entry)

    assert entry.new_value == {"effective_from": "2026-04-01"}


def test_nested_values_are_serialized_too(db, actor_id):
    """A value one level down is still a value that must survive the round trip."""
    entry = record(
        db,
        entity_type=EntityType.EMPLOYEE,
        entity_id=1,
        action=Action.UPDATE,
        old=None,
        new={"salary": {"amount": Decimal("100.500"), "history": [Decimal("1.005")]}},
        actor_id=actor_id,
    )
    db.flush()
    db.expire(entry)

    assert entry.new_value == {"salary": {"amount": "100.500", "history": ["1.005"]}}


# --- the transaction rule -------------------------------------------------------


def test_record_does_not_commit(engine):
    """The audit row must not outlive a mutation that failed. If record() committed,
    a salary change that raised afterwards would leave a log entry describing a
    change that never happened.

    Deliberately not using the `db` fixture. That session joins an outer transaction
    with a savepoint, so a stray commit() inside record() would be absorbed and this
    test would pass while the guarantee was broken - verified by making record()
    commit and watching the suite stay green. A real session on its own connection is
    the only thing that can tell the difference.
    """
    marker = 991001  # an entity_id nothing else uses

    session = Session(bind=engine)
    try:
        actor = session.execute(
            text(
                "INSERT INTO app_user (external_id, display_name) "
                "VALUES ('kc-rollback-test', 'Rollback Test') RETURNING id"
            )
        ).scalar_one()

        record(
            session,
            entity_type=EntityType.SALARY,
            entity_id=marker,
            action=Action.UPDATE,
            old=None,
            new={"amount": "1"},
            actor_id=actor,
        )
        session.rollback()
    finally:
        session.close()

    with engine.connect() as outside:
        survived = outside.execute(
            text("SELECT count(*) FROM audit_log WHERE entity_id = :id"), {"id": marker}
        ).scalar_one()

    assert survived == 0, "record() committed: the row outlived the rolled-back transaction"


def test_the_row_is_visible_to_the_caller_before_commit(db, actor_id):
    """Flushed, not committed: the mutation that follows may need to read it back,
    and a failure to write it must surface here rather than at commit time, where
    there is no longer any context about which change caused it."""
    record(
        db,
        entity_type=EntityType.SALARY,
        entity_id=55,
        action=Action.CREATE,
        old=None,
        new={"amount": "1"},
        actor_id=actor_id,
    )

    found = db.execute(text("SELECT count(*) FROM audit_log WHERE entity_id = 55")).scalar_one()
    assert found == 1


def test_an_unknown_actor_fails_the_write_immediately(db):
    """Not at commit, where the stack trace no longer says which change it was."""
    with pytest.raises(IntegrityError):
        record(
            db,
            entity_type=EntityType.SALARY,
            entity_id=1,
            action=Action.CREATE,
            old=None,
            new={},
            actor_id=999999,
        )


# --- the vocabulary is closed ----------------------------------------------------


def test_an_entity_type_outside_the_vocabulary_is_refused(db, actor_id):
    """The CHECK constraint is the authority. A typo'd entity_type would make the
    row invisible to every query that filters on it - present, and useless."""
    with pytest.raises(IntegrityError, match="ck_audit_entity_type"):
        record(
            db,
            entity_type="SALRY",
            entity_id=1,
            action=Action.CREATE,
            old=None,
            new={},
            actor_id=actor_id,
        )


def test_an_action_outside_the_vocabulary_is_refused(db, actor_id):
    with pytest.raises(IntegrityError, match="ck_audit_action"):
        record(
            db,
            entity_type=EntityType.SALARY,
            entity_id=1,
            action="MODIFIED",
            old=None,
            new={},
            actor_id=actor_id,
        )


def test_the_enum_values_match_what_the_database_allows(db):
    """The two must agree. If they drift, every write with the new value fails at the
    constraint rather than at the type - at runtime, in production."""
    allowed = db.execute(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'ck_audit_entity_type'"
        )
    ).scalar_one()

    for entity_type in EntityType:
        assert f"'{entity_type.value}'" in allowed
