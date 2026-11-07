-- SNAPSHOT. Executed only by Alembic migration 0002. See 001_currency.sql.

-- Unlike currency and country, these are not facts about the world - they are ACME's
-- own structure, and ACME will change them. They are seeded anyway because the
-- alternative is worse: employee.department_id is NOT NULL, the import refuses an
-- unknown department rather than inventing one, and there is no screen for creating
-- departments. An empty table would mean no employee can be created at all, by any
-- route, with no way out.
--
-- So this is a starting point, expected to be edited, not a definition. Retiring one
-- sets is_active = false rather than deleting it, so historical reports still read.
--
-- ON CONFLICT targets lower(name) because that is what ux_department_name indexes:
-- 'Engineering' and 'engineering' are one department, and a group-by that treats them
-- as two splits one department's payroll across two chart rows.

INSERT INTO department (name) VALUES
    ('Engineering'),
    ('Product'),
    ('Design'),
    ('Data'),
    ('Sales'),
    ('Marketing'),
    ('Customer Success'),
    ('Finance'),
    ('People'),
    ('Legal'),
    ('Operations'),
    ('IT')
ON CONFLICT (lower(name)) DO NOTHING;
