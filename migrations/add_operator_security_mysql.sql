-- Stop old application processes first. Run once on an existing admin schema.
ALTER TABLE tq_admin_users ADD COLUMN role VARCHAR(32);
ALTER TABLE tq_admin_users ADD COLUMN password_changed_at DATETIME(6);
UPDATE tq_admin_sessions SET revoked_at = CURRENT_TIMESTAMP WHERE revoked_at IS NULL;

CREATE TABLE tq_admin_operations (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    actor VARCHAR(100) NOT NULL,
    operation VARCHAR(256) NOT NULL,
    target VARCHAR(512) NOT NULL,
    status INTEGER NOT NULL,
    recorded_at DATETIME(6) NOT NULL
);
