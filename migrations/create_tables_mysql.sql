-- Apply to the existing consumer MySQL schema after review.
-- Creates only TekesQuotaKit tq_ tables; does not CREATE DATABASE or USE another schema.

CREATE TABLE tq_clients (
	id BIGINT NOT NULL AUTO_INCREMENT,
	client_id VARCHAR(64) NOT NULL,
	key_hash VARCHAR(64) NOT NULL,
	tenant_id VARCHAR(64) NOT NULL,
	service_code VARCHAR(64) NOT NULL,
	`role` VARCHAR(16) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (client_id),
	UNIQUE (key_hash)
);

CREATE TABLE tq_ledger (
	id BIGINT NOT NULL AUTO_INCREMENT,
	token_hash VARCHAR(64) NOT NULL,
	tenant_id VARCHAR(64) NOT NULL,
	subject_id BIGINT NOT NULL,
	service_code VARCHAR(64) NOT NULL,
	quota_code VARCHAR(64) NOT NULL,
	usage_key VARCHAR(64) NOT NULL,
	unit_code VARCHAR(32) NOT NULL,
	period_start DATETIME(6) NOT NULL,
	event_type VARCHAR(16) NOT NULL,
	delta_units BIGINT NOT NULL,
	created_at DATETIME(6) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_tq_ledger_token_event UNIQUE (token_hash, event_type)
);

CREATE INDEX ix_tq_ledger_subject ON tq_ledger (tenant_id, subject_id, quota_code, period_start);

CREATE TABLE tq_levels (
	id BIGINT NOT NULL AUTO_INCREMENT,
	tenant_id VARCHAR(64) NOT NULL,
	level_code VARCHAR(64) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_tq_level_code UNIQUE (tenant_id, level_code)
);

CREATE TABLE tq_quotas (
	id BIGINT NOT NULL AUTO_INCREMENT,
	tenant_id VARCHAR(64) NOT NULL,
	quota_code VARCHAR(64) NOT NULL,
	unit_code VARCHAR(32) NOT NULL,
	metering_mode VARCHAR(16) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_tq_quota_code UNIQUE (tenant_id, quota_code)
);

CREATE TABLE tq_tokens (
	id BIGINT NOT NULL AUTO_INCREMENT,
	token_hash VARCHAR(64) NOT NULL,
	tenant_id VARCHAR(64) NOT NULL,
	subject_id BIGINT NOT NULL,
	service_code VARCHAR(64) NOT NULL,
	quota_code VARCHAR(64) NOT NULL,
	usage_key VARCHAR(64) NOT NULL,
	request_key VARCHAR(128) NOT NULL,
	issuer_client_id VARCHAR(64) NOT NULL,
	provider_client_id VARCHAR(64),
	status VARCHAR(16) NOT NULL,
	admitted_at DATETIME(6) NOT NULL,
	period_start DATETIME(6),
	period_end DATETIME(6),
	unit_code VARCHAR(32),
	metering_mode VARCHAR(16),
	limit_value BIGINT,
	consumed_units BIGINT,
	session_status VARCHAR(16),
	session_expires_at DATETIME(6),
	closed_at DATETIME(6),
	PRIMARY KEY (id),
	CONSTRAINT uq_tq_token_request UNIQUE (tenant_id, issuer_client_id, subject_id, service_code, request_key),
	UNIQUE (token_hash)
);

CREATE INDEX ix_tq_unsettled ON tq_tokens (status, admitted_at);

CREATE TABLE tq_token_items (
	id BIGINT NOT NULL AUTO_INCREMENT,
	token_id BIGINT NOT NULL,
	child_service_code VARCHAR(64) NOT NULL,
	slot_no INTEGER NOT NULL,
	max_uses INTEGER,
	request_key VARCHAR(128),
	status VARCHAR(16) NOT NULL,
	used_at DATETIME(6),
	PRIMARY KEY (id),
	CONSTRAINT uq_tq_token_item_slot UNIQUE (token_id, child_service_code, slot_no),
	CONSTRAINT uq_tq_token_item_request UNIQUE (token_id, request_key),
	FOREIGN KEY(token_id) REFERENCES tq_tokens (id)
);

CREATE TABLE tq_usage (
	id BIGINT NOT NULL AUTO_INCREMENT,
	tenant_id VARCHAR(64) NOT NULL,
	subject_id BIGINT NOT NULL,
	quota_code VARCHAR(64) NOT NULL,
	period_start DATETIME(6) NOT NULL,
	period_end DATETIME(6) NOT NULL,
	used_units BIGINT NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_tq_usage_period UNIQUE (tenant_id, subject_id, quota_code, period_start)
);

CREATE TABLE tq_assignments (
	id BIGINT NOT NULL AUTO_INCREMENT,
	tenant_id VARCHAR(64) NOT NULL,
	subject_id BIGINT NOT NULL,
	level_code VARCHAR(64) NOT NULL,
	effective_at DATETIME(6) NOT NULL,
	expires_at DATETIME(6),
	term_start DATETIME(6),
	term_end DATETIME(6),
	package_code VARCHAR(64) NOT NULL DEFAULT 'membership',
	sort_order INTEGER NOT NULL DEFAULT 0,
	revoked_at DATETIME(6),
	PRIMARY KEY (id),
	FOREIGN KEY(tenant_id, level_code) REFERENCES tq_levels (tenant_id, level_code)
);

CREATE INDEX ix_tq_assignment_subject ON tq_assignments (tenant_id, subject_id, effective_at);

CREATE TABLE tq_limits (
	id BIGINT NOT NULL AUTO_INCREMENT,
	tenant_id VARCHAR(64) NOT NULL,
	level_code VARCHAR(64) NOT NULL,
	quota_code VARCHAR(64) NOT NULL,
	limit_mode VARCHAR(16) NOT NULL,
	limit_value BIGINT,
	period_kind VARCHAR(16) NOT NULL,
	timezone VARCHAR(64) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_tq_limit_code UNIQUE (tenant_id, level_code, quota_code),
	FOREIGN KEY(tenant_id, level_code) REFERENCES tq_levels (tenant_id, level_code),
	FOREIGN KEY(tenant_id, quota_code) REFERENCES tq_quotas (tenant_id, quota_code)
);

CREATE TABLE tq_services (
	id BIGINT NOT NULL AUTO_INCREMENT,
	tenant_id VARCHAR(64) NOT NULL,
	service_code VARCHAR(64) NOT NULL,
	quota_code VARCHAR(64),
	service_kind VARCHAR(16) NOT NULL,
	redemption_mode VARCHAR(16) NOT NULL,
	charge_units BIGINT,
	session_ttl_seconds INTEGER,
	PRIMARY KEY (id),
	CONSTRAINT uq_tq_service_code UNIQUE (tenant_id, service_code),
	FOREIGN KEY(tenant_id, quota_code) REFERENCES tq_quotas (tenant_id, quota_code)
);

CREATE TABLE tq_service_members (
	id BIGINT NOT NULL AUTO_INCREMENT,
	tenant_id VARCHAR(64) NOT NULL,
	parent_service_code VARCHAR(64) NOT NULL,
	child_service_code VARCHAR(64) NOT NULL,
	max_uses INTEGER,
	PRIMARY KEY (id),
	CONSTRAINT uq_tq_service_member UNIQUE (tenant_id, parent_service_code, child_service_code),
	FOREIGN KEY(tenant_id, parent_service_code) REFERENCES tq_services (tenant_id, service_code),
	FOREIGN KEY(tenant_id, child_service_code) REFERENCES tq_services (tenant_id, service_code)
);

-- Web console accounts (0.3.0).

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

-- Registered business systems (0.4.0).

CREATE TABLE tq_tenants (
	id BIGINT NOT NULL AUTO_INCREMENT,
	tenant_id VARCHAR(64) NOT NULL,
	name VARCHAR(100) NOT NULL,
	subject_id_definition VARCHAR(500),
	created_at DATETIME(6) NOT NULL,
	updated_at DATETIME(6) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (tenant_id)
);

-- Auxiliary service quota packages. Existing grants are immutable template snapshots.
CREATE TABLE tq_packages (
	id BIGINT NOT NULL AUTO_INCREMENT,
	tenant_id VARCHAR(64) NOT NULL,
	package_code VARCHAR(64) NOT NULL,
	name VARCHAR(100) NOT NULL,
	service_code VARCHAR(64) NOT NULL,
	units BIGINT NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_tq_package UNIQUE (tenant_id, package_code),
	FOREIGN KEY(tenant_id, service_code) REFERENCES tq_services (tenant_id, service_code)
);

CREATE TABLE tq_package_grants (
	id BIGINT NOT NULL AUTO_INCREMENT,
	tenant_id VARCHAR(64) NOT NULL,
	subject_id BIGINT NOT NULL,
	grant_code VARCHAR(64) NOT NULL,
	package_code VARCHAR(64) NOT NULL,
	service_code VARCHAR(64) NOT NULL,
	quota_code VARCHAR(64) NOT NULL,
	total_units BIGINT NOT NULL,
	used_units BIGINT NOT NULL,
	effective_at DATETIME(6) NOT NULL,
	expires_at DATETIME(6),
	revoked_at DATETIME(6),
	PRIMARY KEY (id),
	CONSTRAINT uq_tq_package_grant UNIQUE (tenant_id, subject_id, grant_code)
);

CREATE INDEX ix_tq_package_subject ON tq_package_grants (tenant_id, subject_id, service_code);
CREATE TABLE tq_package_charges (
	id BIGINT NOT NULL AUTO_INCREMENT,
	token_id BIGINT NOT NULL,
	grant_id BIGINT NOT NULL,
	units BIGINT NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_tq_package_charge UNIQUE (token_id, grant_id),
	FOREIGN KEY(token_id) REFERENCES tq_tokens (id),
	FOREIGN KEY(grant_id) REFERENCES tq_package_grants (id)
);

-- Ordered Level packages (0.10.0).
CREATE TABLE tq_subject_packages (
	id BIGINT NOT NULL AUTO_INCREMENT,
	tenant_id VARCHAR(64) NOT NULL,
	subject_id BIGINT NOT NULL,
	revision INTEGER NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_tq_subject_packages UNIQUE (tenant_id, subject_id)
);

CREATE TABLE tq_level_package_usage (
	id BIGINT NOT NULL AUTO_INCREMENT,
	assignment_id BIGINT NOT NULL,
	quota_code VARCHAR(64) NOT NULL,
	period_start DATETIME(6) NOT NULL,
	period_end DATETIME(6) NOT NULL,
	used_units BIGINT NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_tq_level_usage UNIQUE (assignment_id, quota_code, period_start),
	FOREIGN KEY(assignment_id) REFERENCES tq_assignments (id)
);

CREATE TABLE tq_level_package_admissions (
	id BIGINT NOT NULL AUTO_INCREMENT,
	token_id BIGINT NOT NULL,
	assignment_id BIGINT NOT NULL,
	sort_order INTEGER NOT NULL,
	period_start DATETIME(6) NOT NULL,
	period_end DATETIME(6) NOT NULL,
	limit_value BIGINT,
	PRIMARY KEY (id),
	CONSTRAINT uq_tq_level_admission UNIQUE (token_id, assignment_id),
	FOREIGN KEY(token_id) REFERENCES tq_tokens (id),
	FOREIGN KEY(assignment_id) REFERENCES tq_assignments (id)
);

CREATE TABLE tq_level_package_charges (
	id BIGINT NOT NULL AUTO_INCREMENT,
	token_id BIGINT NOT NULL,
	assignment_id BIGINT NOT NULL,
	legacy_usage_id BIGINT,
	package_usage_id BIGINT,
	units BIGINT NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(token_id) REFERENCES tq_tokens (id),
	FOREIGN KEY(assignment_id) REFERENCES tq_assignments (id),
	FOREIGN KEY(legacy_usage_id) REFERENCES tq_usage (id),
	FOREIGN KEY(package_usage_id) REFERENCES tq_level_package_usage (id)
);
CREATE INDEX ix_tq_level_charge_token ON tq_level_package_charges (token_id);
