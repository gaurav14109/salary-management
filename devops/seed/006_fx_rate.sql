-- SNAPSHOT. Executed only by Alembic migration 0002. See 001_currency.sql.

-- One indicative rate per currency, so a fresh database can convert a salary.
--
-- READ THIS BEFORE TRUSTING THESE NUMBERS. They are a single snapshot, roughly
-- accurate for 2026, and they are dated 2020-01-01 - which is not when they were
-- true. That is a deliberate trade, and it is the lesser of two bad options:
--
--   * dated recently, every salary effective before that date fails with
--     MissingFxRate, so no historical import works on a fresh database;
--   * dated early, every salary date resolves, but a 2021 salary converts at a 2026
--     rate.
--
-- The second is wrong by a margin; the first is unusable. What makes the second
-- survivable is that it is wrong *visibly* and is designed to be superseded: the
-- lookup takes the most recent rate on or before the salary date, so an admin
-- posting real dated rates overrides these for every date after the one they post,
-- without deleting anything. These are a floor, not an answer.
--
-- What they are not, under any circumstance, is a fallback to 1.0. A missing rate
-- raises; a wrong rate is at least the right order of magnitude and shows up in a
-- dashboard as a number someone questions.
--
-- USD is 1 by definition - the reporting currency against itself. The application
-- short-circuits USD before it queries this table, so the row is here to make the
-- table self-describing rather than because anything reads it.

INSERT INTO fx_rate (currency, units_per_usd, as_of, created_by)
SELECT code, units, DATE '2020-01-01', (SELECT id FROM app_user WHERE external_id = 'system:seed')
FROM (VALUES
    ('USD',          1.00000000),   -- the reporting currency, by definition
    ('EUR',          0.92000000),
    ('GBP',          0.79000000),
    ('INR',         83.45000000),
    ('AUD',          1.52000000),
    ('CAD',          1.36000000),
    ('CHF',          0.88000000),
    ('CNY',          7.24000000),
    ('SGD',          1.34000000),
    ('HKD',          7.82000000),
    ('NZD',          1.64000000),
    ('SEK',         10.45000000),
    ('NOK',         10.65000000),
    ('DKK',          6.86000000),
    ('PLN',          3.98000000),
    ('CZK',         23.20000000),
    ('ZAR',         18.60000000),
    ('BRL',          5.05000000),
    ('MXN',         17.10000000),
    ('ARS',        880.00000000),
    ('AED',          3.67250000),   -- pegged
    ('SAR',          3.75000000),   -- pegged
    ('ILS',          3.70000000),
    ('TRY',         32.20000000),
    ('THB',         35.80000000),
    ('MYR',          4.72000000),
    ('IDR',      15700.00000000),
    ('PHP',         56.10000000),
    ('VND',      24800.00000000),
    ('KRW',       1330.00000000),
    ('JPY',        151.00000000),
    ('KWD',          0.30684000),   -- three minor units
    ('BHD',          0.37600000),
    ('OMR',          0.38450000),
    ('JOD',          0.70900000),
    ('TND',          3.12000000)
) AS seed (code, units)
ON CONFLICT (currency, as_of) DO NOTHING;
