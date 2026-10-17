ALTER TABLE tq_services ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'enabled';
UPDATE tq_services SET status = CASE WHEN enabled = 1 THEN 'enabled' ELSE 'disabled' END;
