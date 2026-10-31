-- One-time additive upgrade for an existing SQLite consumer database (v0.16.2+).
-- Run against the application's current database file, after making a backup.
-- Statements are safe to re-run; no existing quota table or row is changed.

CREATE TABLE IF NOT EXISTS tq_default_plans (
	id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
	tenant_id VARCHAR(64) NOT NULL,
	plan_id VARCHAR(128) NOT NULL,
	name VARCHAR(100) NOT NULL,
	enabled BOOLEAN NOT NULL,
	revision INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	CONSTRAINT uq_tq_default_plan UNIQUE (tenant_id, plan_id)
);

CREATE TABLE IF NOT EXISTS tq_default_plan_revisions (
	id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
	plan_id INTEGER NOT NULL,
	revision INTEGER NOT NULL,
	name VARCHAR(100) NOT NULL,
	enabled BOOLEAN NOT NULL,
	packages JSON NOT NULL,
	created_at DATETIME NOT NULL,
	CONSTRAINT uq_tq_default_plan_revision UNIQUE (plan_id, revision),
	FOREIGN KEY(plan_id) REFERENCES tq_default_plans (id)
);

CREATE TABLE IF NOT EXISTS tq_default_initializations (
	id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
	initialization_id VARCHAR(128) NOT NULL UNIQUE,
	tenant_id VARCHAR(64) NOT NULL,
	subject_id VARCHAR(128) NOT NULL,
	plan_revision_id INTEGER NOT NULL,
	default_plan_id VARCHAR(128) NOT NULL,
	plan_revision INTEGER NOT NULL,
	source JSON NOT NULL,
	applied_at DATETIME NOT NULL,
	CONSTRAINT uq_tq_default_initialization UNIQUE (tenant_id, subject_id),
	FOREIGN KEY(plan_revision_id) REFERENCES tq_default_plan_revisions (id)
);

CREATE TABLE IF NOT EXISTS tq_default_initialization_packages (
	id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
	initialization_id INTEGER NOT NULL,
	assignment_id INTEGER NOT NULL UNIQUE,
	sort_order INTEGER NOT NULL,
	code VARCHAR(64) NOT NULL,
	name VARCHAR(100) NOT NULL,
	limits JSON NOT NULL,
	CONSTRAINT uq_tq_default_package_order UNIQUE (initialization_id, sort_order),
	FOREIGN KEY(initialization_id) REFERENCES tq_default_initializations (id),
	FOREIGN KEY(assignment_id) REFERENCES tq_assignments (id)
);
