-- SNAPSHOT. This file is executed only by Alembic migration 0001.
-- Editing it changes what a fresh database gets and nothing else: a database
-- already past 0001 will never see the change, and the two diverge silently.
-- Schema changes go in a new alembic revision. See backend/README.md.

-- Admin-maintained. One rate per currency per day.

CREATE TABLE fx_rate (
    id            SERIAL         PRIMARY KEY,
    currency      CHAR(3)        NOT NULL REFERENCES currency (code),
    units_per_usd NUMERIC(18, 8) NOT NULL,
    as_of         DATE           NOT NULL,
    created_by    INTEGER        NOT NULL REFERENCES app_user (id),
    created_at    TIMESTAMPTZ    NOT NULL DEFAULT now(),

    CONSTRAINT ck_fx_rate_positive CHECK (units_per_usd > 0)
);

-- One rate per currency per day. Without this an admin entering a correction creates
-- a second rate for the same day and the lookup silently picks one of them.
CREATE UNIQUE INDEX ux_fx_currency_date ON fx_rate (currency, as_of);

COMMENT ON COLUMN fx_rate.units_per_usd IS
    '83.45 means 83.45 INR per 1 USD. Lookup takes the most recent rate on or before the '
    'salary date; there is no fallback to 1.0 under any condition.';
