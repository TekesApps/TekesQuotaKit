-- Apply once before starting 0.13.0. Existing services stay enabled.
-- Compatible with the old server; rollback of application code can leave this column in place.
ALTER TABLE tq_services ADD COLUMN enabled BOOLEAN NOT NULL DEFAULT TRUE AFTER service_code;
