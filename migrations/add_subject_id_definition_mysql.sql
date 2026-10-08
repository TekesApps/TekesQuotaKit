-- Adds the operator-written user ID definition to tq_tenants (0.6.0).
-- Apply once to a 0.4.x or 0.5.x schema, before starting 0.6.0. `init-schema` cannot add columns.
-- The column is nullable, so 0.4.x and 0.5.x keep working after it is added.

ALTER TABLE tq_tenants ADD COLUMN subject_id_definition VARCHAR(500) NULL AFTER name;
