-- SNAPSHOT. Executed only by Alembic migration 0002. See 001_currency.sql.

-- An actor for rows the system creates rather than a person.
--
-- fx_rate.created_by is NOT NULL, deliberately: every rate has to be attributable,
-- because an FX rate moves every reported figure in the currency it applies to.
-- Seeded rates still need an author, and inventing a fake human would make the audit
-- trail lie. This row is honest about what it is.
--
-- external_id is 'system:seed' rather than a UUID, so it cannot collide with a
-- Keycloak subject and is obviously not a person when it appears in a report.
-- Nobody can log in as it: there is no such user in the realm, and this service
-- never authenticates anyone anyway.

INSERT INTO app_user (external_id, email, display_name) VALUES
    ('system:seed', NULL, 'System (seed data)')
ON CONFLICT (external_id) DO NOTHING;
