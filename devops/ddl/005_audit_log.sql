-- SNAPSHOT. This file is executed only by Alembic migration 0001.
-- Editing it changes what a fresh database gets and nothing else: a database
-- already past 0001 will never see the change, and the two diverge silently.
-- Schema changes go in a new alembic revision. See backend/README.md.

-- Append-only. Answers "who changed this and when" - a compliance fact, deliberately
-- separate from salary history, which is a business fact that feeds every report.
--
-- Collapsing the two means either polluting payroll totals with correction records or
-- losing the ability to prove who made a change.

CREATE TABLE audit_log (
    id          BIGSERIAL    PRIMARY KEY,
    entity_type VARCHAR(30)  NOT NULL,
    entity_id   INTEGER      NOT NULL,
    action      VARCHAR(20)  NOT NULL,
    old_value   JSONB,
    new_value   JSONB,
    actor_id    INTEGER      NOT NULL REFERENCES app_user (id),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT ck_audit_entity_type CHECK (
        entity_type IN ('EMPLOYEE', 'SALARY', 'FX_RATE', 'DEPARTMENT', 'BAND')
    ),
    CONSTRAINT ck_audit_action CHECK (action IN ('CREATE', 'UPDATE', 'DELETE'))
);

COMMENT ON COLUMN audit_log.entity_id IS
    'Polymorphic, so deliberately not a foreign key. One audit table per entity would '
    'triple the schema to buy referential integrity on an append-only log that no report '
    'joins. The mitigation is that only the audit service writes here.';
COMMENT ON COLUMN audit_log.old_value IS
    'Decimals are stored as strings so no precision is lost through JSON number handling.';

CREATE INDEX ix_audit_entity ON audit_log (entity_type, entity_id, created_at DESC);
