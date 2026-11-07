-- SNAPSHOT. Executed only by Alembic migration 0002.
-- Editing this file changes what a fresh database gets and nothing else: a database
-- already past 0002 will never see the change. New reference values go in a new
-- revision. See devops/README.md.

-- ISO 4217. These are facts about the world, not configuration: nobody at ACME
-- decides that the Kuwaiti dinar has three decimal places. Seeded rather than
-- entered, because an empty currency table means no salary and no FX rate can be
-- created at all - every one of them is a foreign key into this.
--
-- minor_units drives display and catches cents entered for a zero-decimal currency.
-- The three-decimal entries are why salary.amount is NUMERIC(14,3): at two places a
-- Kuwaiti salary silently loses its last digit.

INSERT INTO currency (code, name, minor_units) VALUES
    ('USD', 'US Dollar',             2),
    ('EUR', 'Euro',                  2),
    ('GBP', 'Pound Sterling',        2),
    ('INR', 'Indian Rupee',          2),
    ('AUD', 'Australian Dollar',     2),
    ('CAD', 'Canadian Dollar',       2),
    ('CHF', 'Swiss Franc',           2),
    ('CNY', 'Yuan Renminbi',         2),
    ('SGD', 'Singapore Dollar',      2),
    ('HKD', 'Hong Kong Dollar',      2),
    ('NZD', 'New Zealand Dollar',    2),
    ('SEK', 'Swedish Krona',         2),
    ('NOK', 'Norwegian Krone',       2),
    ('DKK', 'Danish Krone',          2),
    ('PLN', 'Zloty',                 2),
    ('CZK', 'Czech Koruna',          2),
    ('ZAR', 'Rand',                  2),
    ('BRL', 'Brazilian Real',        2),
    ('MXN', 'Mexican Peso',          2),
    ('ARS', 'Argentine Peso',        2),
    ('AED', 'UAE Dirham',            2),
    ('SAR', 'Saudi Riyal',           2),
    ('ILS', 'New Israeli Sheqel',    2),
    ('TRY', 'Turkish Lira',          2),
    ('THB', 'Baht',                  2),
    ('MYR', 'Malaysian Ringgit',     2),
    ('IDR', 'Rupiah',                2),
    ('PHP', 'Philippine Peso',       2),
    ('VND', 'Dong',                  0),
    ('KRW', 'Won',                   0),
    ('JPY', 'Yen',                   0),
    -- Three decimal places. The reason the money column is NUMERIC(14,3).
    ('KWD', 'Kuwaiti Dinar',         3),
    ('BHD', 'Bahraini Dinar',        3),
    ('OMR', 'Rial Omani',            3),
    ('JOD', 'Jordanian Dinar',       3),
    ('TND', 'Tunisian Dinar',        3)
ON CONFLICT (code) DO NOTHING;
