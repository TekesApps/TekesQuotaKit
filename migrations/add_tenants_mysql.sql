-- Adds the business system registry to an existing 0.3.x TekesQuotaKit MySQL schema.
-- Only creates one new table; existing tables and data are untouched.
-- `tekes-quota-kit init-schema` creates the same table and is an alternative to this file.

CREATE TABLE tq_tenants (
	id BIGINT NOT NULL AUTO_INCREMENT,
	tenant_id VARCHAR(64) NOT NULL,
	name VARCHAR(100) NOT NULL,
	created_at DATETIME(6) NOT NULL,
	updated_at DATETIME(6) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (tenant_id)
);
