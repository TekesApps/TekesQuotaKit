CREATE TABLE tq_membership_purchases (
	id BIGINT NOT NULL AUTO_INCREMENT,
	tenant_id VARCHAR(64) NOT NULL,
	subject_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
	order_code VARCHAR(64) NOT NULL,
	level_code VARCHAR(64) NOT NULL,
	expected_level VARCHAR(64) NOT NULL,
	period_days INTEGER NOT NULL,
	assignment_id BIGINT,
	created_at DATETIME(6) NOT NULL,
	revoked_at DATETIME(6),
	PRIMARY KEY (id),
	CONSTRAINT uq_tq_membership_purchase UNIQUE (tenant_id, order_code),
	FOREIGN KEY(assignment_id) REFERENCES tq_assignments (id)
);
