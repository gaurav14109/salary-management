-- SNAPSHOT. Executed only by Alembic migration 0002. See 003_department.sql, which
-- explains why ACME's own structure is seeded at all.

-- rank is the point of this table. Bands are ordered and text is not: sorted as text
-- they come out L1, L10, L11, L2, and an average-pay-by-band chart in that order is
-- meaningless. 1 is most junior.
--
-- Gaps are not left between ranks. ux_band_rank makes each one unique, so inserting a
-- band between two existing ones means renumbering - which is a deliberate, visible
-- migration rather than a number quietly chosen to fit.

INSERT INTO band (name, rank) VALUES
    ('L1', 1),
    ('L2', 2),
    ('L3', 3),
    ('L4', 4),
    ('L5', 5),
    ('L6', 6),
    ('L7', 7)
ON CONFLICT (lower(name)) DO NOTHING;
