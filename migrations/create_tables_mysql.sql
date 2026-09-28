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
