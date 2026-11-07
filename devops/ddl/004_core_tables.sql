-- SNAPSHOT. This file is executed only by Alembic migration 0001.
-- Editing it changes what a fresh database gets and nothing else: a database
-- already past 0001 will never see the change, and the two diverge silently.
-- Schema changes go in a new alembic revision. See backend/README.md.

-- The two tables the product is about.

CREATE TABLE employee (
    id               SERIAL       PRIMARY KEY,
    employee_code    VARCHAR(50)  NOT NULL UNIQUE,   -- the ID HR already uses; the import match key
    first_name       VARCHAR(100) NOT NULL,
    last_name        VARCHAR(100) NOT NULL,
    email            VARCHAR(255) UNIQUE,            -- nullable; Postgres allows many NULLs under UNIQUE
    country_code     CHAR(2)      NOT NULL REFERENCES country (code),
    department_id    INTEGER      NOT NULL REFERENCES department (id),
    job_title        VARCHAR(150) NOT NULL,
    band_id          INTEGER      REFERENCES band (id),  -- nullable: not every role is banded
    status           VARCHAR(20)  NOT NULL DEFAULT 'ACTIVE',
    hire_date        DATE         NOT NULL,
    termination_date DATE,
    import_job_id    INTEGER      REFERENCES import_job (id),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT ck_employee_status CHECK (status IN ('ACTIVE', 'INACTIVE')),

    -- A leaver must carry the date they left. set_status closes their open salary
    -- row with that date in the same transaction.
    CONSTRAINT ck_employee_inactive_dated
        CHECK (status = 'ACTIVE' OR termination_date IS NOT NULL),

    CONSTRAINT ck_employee_termination_after_hire
        CHECK (termination_date IS NULL OR termination_date >= hire_date)
);

COMMENT ON COLUMN employee.band_id IS
    'Nullable on purpose: contractors and executives commonly sit outside a banding '
    'system, and junk in a required field is worse than an empty one.';


CREATE TABLE salary (
    id               SERIAL         PRIMARY KEY,
    employee_id      INTEGER        NOT NULL REFERENCES employee (id),

    -- Money is NUMERIC, never a float. amount is in the currency the employee is
    -- PAID in, which is not inferable from their country: an expat on a US contract
    -- is paid in USD wherever they sit.
    --
    -- Three decimal places, not two, because KWD, BHD, OMR and JOD have three. At
    -- two a Kuwaiti salary would silently lose its last digit, and that surfaces six
    -- months later from Finance. Widening is free now and a backfill later.
    -- currency.minor_units says how many places to display.
    amount           NUMERIC(14, 3) NOT NULL,
    currency         CHAR(3)        NOT NULL REFERENCES currency (code),

    -- Frozen at write time, never recomputed. Converting at read time would mean
    -- correcting one FX rate silently rewrites a payroll total that Finance already
    -- received. This duplication is the point, not an oversight.
    usd_amount       NUMERIC(14, 2) NOT NULL,   -- USD has two places; no widening needed
    fx_units_per_usd NUMERIC(18, 8) NOT NULL,
    fx_rate_date     DATE           NOT NULL,

    effective_from   DATE           NOT NULL,   -- inclusive
    effective_to     DATE,                      -- inclusive; NULL means current
    change_reason    VARCHAR(30),
    created_by       INTEGER        NOT NULL REFERENCES app_user (id),
    import_job_id    INTEGER        REFERENCES import_job (id),
    created_at       TIMESTAMPTZ    NOT NULL DEFAULT now(),

    CONSTRAINT ck_salary_positive CHECK (amount > 0 AND usd_amount > 0),
    CONSTRAINT ck_salary_rate_positive CHECK (fx_units_per_usd > 0),
    CONSTRAINT ck_salary_range CHECK (effective_to IS NULL OR effective_to >= effective_from),
    CONSTRAINT ck_salary_change_reason CHECK (
        change_reason IS NULL OR change_reason IN (
            'NEW_HIRE', 'PROMOTION', 'MERIT', 'MARKET_ADJUSTMENT', 'CORRECTION', 'IMPORT'
        )
    )
);

COMMENT ON TABLE salary IS
    'Not a field on employee: a pay amount that was true over a date range. One row per '
    'change, exactly one open at a time. Without this, "what did we pay in Q2" has no answer.';
COMMENT ON COLUMN salary.fx_units_per_usd IS
    'Units of local currency per 1 USD, so usd_amount = amount / fx_units_per_usd. Named '
    'this way deliberately: a column called rate_to_usd reads as something to multiply by, '
    'and multiplying 1,200,000 INR by 83.45 overstates that salary roughly 7,000 times.';
