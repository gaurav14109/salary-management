-- SNAPSHOT. This file is executed only by Alembic migration 0001.
-- Editing it changes what a fresh database gets and nothing else: a database
-- already past 0001 will never see the change, and the two diverge silently.
-- Schema changes go in a new alembic revision. See backend/README.md.

-- Reference tables: small, static, and the reason the dashboard can be trusted.
--
-- These hold values an enum could have held. They are tables because the dashboard
-- GROUPs BY all four, and a group-by over free text produces numbers that are wrong
-- without anything failing: 'Engineering', 'engineering' and 'Eng ' become three
-- departments, splitting one department's payroll across three chart rows.
--
-- The import never creates a row here. An unknown department is a row error carrying
-- its row number, not a silently created record.

CREATE TABLE country (
    code        CHAR(2)      PRIMARY KEY,   -- ISO 3166-1 alpha-2
    name        VARCHAR(100) NOT NULL       -- the dashboard shows 'Germany', not 'DE'
);

COMMENT ON TABLE  country      IS 'ISO 3166-1 countries. Seeded by migration.';
COMMENT ON COLUMN country.code IS 'The country of the employing entity, not where the person physically sits.';


CREATE TABLE currency (
    code        CHAR(3)      PRIMARY KEY,   -- ISO 4217 alphabetic: USD, INR, EUR
    name        VARCHAR(100) NOT NULL,
    minor_units SMALLINT     NOT NULL,      -- 2 for USD, 0 for JPY, 3 for KWD

    CONSTRAINT ck_currency_minor_units CHECK (minor_units BETWEEN 0 AND 4)
);

COMMENT ON COLUMN currency.minor_units IS
    'Decimal places. Drives display, and catches cents entered for a zero-decimal currency.';


CREATE TABLE department (
    id        SERIAL       PRIMARY KEY,
    name      VARCHAR(100) NOT NULL,
    is_active BOOLEAN      NOT NULL DEFAULT TRUE
);

-- Unique regardless of case, so an import cannot create 'Engineering'
-- alongside an existing 'engineering'.
CREATE UNIQUE INDEX ux_department_name ON department (lower(name));

COMMENT ON COLUMN department.is_active IS
    'A retired department stays readable in historical reports rather than being deleted.';


CREATE TABLE band (
    id        SERIAL      PRIMARY KEY,
    name      VARCHAR(50) NOT NULL,
    rank      SMALLINT    NOT NULL,         -- 1 is most junior
    is_active BOOLEAN     NOT NULL DEFAULT TRUE,

    CONSTRAINT ck_band_rank_positive CHECK (rank > 0)
);

CREATE UNIQUE INDEX ux_band_name ON band (lower(name));
CREATE UNIQUE INDEX ux_band_rank ON band (rank);

COMMENT ON COLUMN band.rank IS
    'Sort order, and the reason band is a table rather than an enum: bands are ordered '
    'and text is not, so a text column sorts L1, L10, L11, L2 and an average-pay-by-band '
    'chart in that order is meaningless.';
