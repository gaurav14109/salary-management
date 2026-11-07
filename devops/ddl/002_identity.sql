-- SNAPSHOT. This file is executed only by Alembic migration 0001.
-- Editing it changes what a fresh database gets and nothing else: a database
-- already past 0001 will never see the change, and the two diverge silently.
-- Schema changes go in a new alembic revision. See backend/README.md.

-- Who logs in. Named app_user because "user" is reserved in Postgres and quoting
-- it in every query is a permanent papercut.
--
-- Identity lives in Keycloak. This application never sees a password, never stores
-- one and never issues a token, so there is no password hash, no role and no enabled
-- flag here: the token carries the roles, and a local copy of an authorization fact
-- drifts the moment someone changes a role in Keycloak - and drifts open.
--
-- The row exists so audit_log.actor_id has a stable local target. "Who changed this
-- salary" must still answer in three years, from the database alone, without an
-- external service being reachable or a realm that has since been rebuilt.

CREATE TABLE app_user (
    id           SERIAL       PRIMARY KEY,
    external_id  VARCHAR(255) NOT NULL UNIQUE,   -- Keycloak 'sub'
    email        VARCHAR(255),
    display_name VARCHAR(200),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

COMMENT ON COLUMN app_user.external_id IS
    'The Keycloak subject claim. Keyed on this rather than email because people change '
    'email, and an audit trail keyed on a mutable field is not an audit trail.';
COMMENT ON COLUMN app_user.email IS
    'Copied from the token for display. Rewritten only when it differs from the token, so '
    'the steady state of a request is one SELECT and no write. Deliberately not authoritative.';

-- There is no last_seen_at. Tracking it would turn every GET into a write
-- transaction, all of them contending for the same row, in exchange for a column
-- no feature reads - the same reason this model carries no manager_id.
