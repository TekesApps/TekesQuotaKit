-- One-time v0.9.x -> v0.10.0 migration. Existing membership and usage stay intact.
ALTER TABLE tq_assignments
 ADD COLUMN package_code VARCHAR(64) NOT NULL DEFAULT 'membership',
 ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0,
 ADD COLUMN revoked_at DATETIME(6);

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
