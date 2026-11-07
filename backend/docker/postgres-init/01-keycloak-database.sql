-- Keycloak gets its own database and its own role, on the same server for local
-- convenience only.
--
-- Not the same database as the salary data: restoring last Tuesday's payroll would
-- otherwise roll back every password change and new account since Tuesday. Two
-- unrelated failure domains should not share a restore point.
--
-- Separate roles as well, so the API's credentials cannot read the identity tables
-- and Keycloak's cannot read salaries. In production these are separate instances.
--
-- Runs once, on first initialisation of an empty data volume.

CREATE ROLE keycloak WITH LOGIN PASSWORD 'keycloak';
CREATE DATABASE keycloak OWNER keycloak;

-- The salary user has no business in Keycloak's schema, and vice versa.
REVOKE ALL ON DATABASE keycloak FROM PUBLIC;
GRANT ALL PRIVILEGES ON DATABASE keycloak TO keycloak;
