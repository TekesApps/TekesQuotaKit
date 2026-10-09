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
