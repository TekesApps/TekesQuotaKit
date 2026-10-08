-- Adds web console accounts to an existing 0.2.x TekesQuotaKit MySQL schema.
-- Only creates three new tables; existing tables and data are untouched.
-- `tekes-quota-kit init-schema` creates the same tables and is an alternative to this file.

CREATE TABLE tq_admin_users (
	id BIGINT NOT NULL AUTO_INCREMENT,
	username VARCHAR(100) NOT NULL,
	password_hash VARCHAR(160) NOT NULL,
	status VARCHAR(16) NOT NULL,
	created_at DATETIME(6) NOT NULL,
	updated_at DATETIME(6) NOT NULL,
	last_login_at DATETIME(6),
	PRIMARY KEY (id),
	UNIQUE (username)
);

CREATE TABLE tq_admin_sessions (
	id BIGINT NOT NULL AUTO_INCREMENT,
	token_hash VARCHAR(64) NOT NULL,
	admin_user_id BIGINT NOT NULL,
	created_at DATETIME(6) NOT NULL,
	expires_at DATETIME(6) NOT NULL,
	revoked_at DATETIME(6),
	PRIMARY KEY (id),
	FOREIGN KEY(admin_user_id) REFERENCES tq_admin_users (id),
	UNIQUE (token_hash)
);

CREATE INDEX ix_tq_admin_session_user ON tq_admin_sessions (admin_user_id);

CREATE TABLE tq_admin_login_attempts (
	id BIGINT NOT NULL AUTO_INCREMENT,
	username VARCHAR(100) NOT NULL,
	success BOOL NOT NULL,
	attempted_at DATETIME(6) NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_tq_admin_login_attempt ON tq_admin_login_attempts (username, attempted_at);
