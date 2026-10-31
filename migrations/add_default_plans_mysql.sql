-- One-time upgrade for an existing v0.16.2+ consumer schema.
-- Apply in order on the existing schema; this file does not select or create a database.
-- Review and back up the target schema first. Do not execute against production from this repo.

CREATE TABLE tq_default_plans (
	id BIGINT NOT NULL AUTO_INCREMENT,
	tenant_id VARCHAR(64) NOT NULL,
	plan_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	name VARCHAR(100) NOT NULL,
	enabled BOOL NOT NULL,
	revision INTEGER NOT NULL,
	created_at DATETIME(6) NOT NULL,
	updated_at DATETIME(6) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_tq_default_plan UNIQUE (tenant_id, plan_id)
);

CREATE TABLE tq_default_plan_revisions (
	id BIGINT NOT NULL AUTO_INCREMENT,
	plan_id BIGINT NOT NULL,
	revision INTEGER NOT NULL,
	name VARCHAR(100) NOT NULL,
	enabled BOOL NOT NULL,
	packages JSON NOT NULL,
	created_at DATETIME(6) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_tq_default_plan_revision UNIQUE (plan_id, revision),
	FOREIGN KEY(plan_id) REFERENCES tq_default_plans (id)
);

CREATE TABLE tq_default_initializations (
	id BIGINT NOT NULL AUTO_INCREMENT,
	initialization_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	tenant_id VARCHAR(64) NOT NULL,
	subject_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	plan_revision_id BIGINT NOT NULL,
	default_plan_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	plan_revision INTEGER NOT NULL,
	source JSON NOT NULL,
	applied_at DATETIME(6) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_tq_default_initialization UNIQUE (tenant_id, subject_id),
	FOREIGN KEY(plan_revision_id) REFERENCES tq_default_plan_revisions (id),
	UNIQUE (initialization_id)
);

CREATE TABLE tq_default_initialization_packages (
	id BIGINT NOT NULL AUTO_INCREMENT,
	initialization_id BIGINT NOT NULL,
	assignment_id BIGINT NOT NULL,
	sort_order INTEGER NOT NULL,
	code VARCHAR(64) NOT NULL,
	name VARCHAR(100) NOT NULL,
	limits JSON NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_tq_default_package_order UNIQUE (initialization_id, sort_order),
	FOREIGN KEY(initialization_id) REFERENCES tq_default_initializations (id),
	FOREIGN KEY(assignment_id) REFERENCES tq_assignments (id),
	UNIQUE (assignment_id)
);
