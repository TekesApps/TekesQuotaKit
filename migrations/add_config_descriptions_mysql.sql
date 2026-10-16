-- Apply once to an existing 0.11.x schema BEFORE deploying description support.
-- Back up tq_ tables and stop service/maintenance writers first.
-- Nullable metadata only; no code, limit, assignment or accounting values change.
ALTER TABLE tq_services ADD COLUMN description VARCHAR(2000) NULL;
ALTER TABLE tq_levels ADD COLUMN description VARCHAR(2000) NULL;
ALTER TABLE tq_quotas ADD COLUMN description VARCHAR(2000) NULL;
ALTER TABLE tq_limits ADD COLUMN description VARCHAR(2000) NULL;
