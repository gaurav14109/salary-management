-- SNAPSHOT. Executed only by Alembic migration 0002. See 001_currency.sql.

-- ISO 3166-1 alpha-2, for the countries ACME employs in. The dashboard groups by
-- this and shows the name, not the code - 'Germany', not 'DE'.
--
-- Not the full ISO list. 249 rows of which 40 are used makes a country dropdown that
-- is worse to use than a short one, and adding a country is one INSERT in a new
-- revision when ACME opens an office there.
--
-- This is the country of the employing entity, not where the person physically sits:
-- an expat on a US contract is employed in the US wherever they are.

INSERT INTO country (code, name) VALUES
    ('US', 'United States'),
    ('CA', 'Canada'),
    ('MX', 'Mexico'),
    ('BR', 'Brazil'),
    ('AR', 'Argentina'),
    ('GB', 'United Kingdom'),
    ('IE', 'Ireland'),
    ('DE', 'Germany'),
    ('FR', 'France'),
    ('ES', 'Spain'),
    ('IT', 'Italy'),
    ('NL', 'Netherlands'),
    ('BE', 'Belgium'),
    ('CH', 'Switzerland'),
    ('AT', 'Austria'),
    ('SE', 'Sweden'),
    ('NO', 'Norway'),
    ('DK', 'Denmark'),
    ('FI', 'Finland'),
    ('PL', 'Poland'),
    ('CZ', 'Czechia'),
    ('PT', 'Portugal'),
    ('RO', 'Romania'),
    ('TR', 'Turkiye'),
    ('IL', 'Israel'),
    ('AE', 'United Arab Emirates'),
    ('SA', 'Saudi Arabia'),
    ('KW', 'Kuwait'),
    ('BH', 'Bahrain'),
    ('OM', 'Oman'),
    ('JO', 'Jordan'),
    ('ZA', 'South Africa'),
    ('IN', 'India'),
    ('CN', 'China'),
    ('JP', 'Japan'),
    ('KR', 'South Korea'),
    ('SG', 'Singapore'),
    ('HK', 'Hong Kong'),
    ('MY', 'Malaysia'),
    ('ID', 'Indonesia'),
    ('TH', 'Thailand'),
    ('VN', 'Vietnam'),
    ('PH', 'Philippines'),
    ('AU', 'Australia'),
    ('NZ', 'New Zealand')
ON CONFLICT (code) DO NOTHING;
