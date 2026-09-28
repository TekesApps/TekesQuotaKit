-- One-time migration for an existing TekesQuotaKit MySQL schema.
-- Back up the application schema first. Do not rerun this file after it succeeds.
-- Existing atomic services and tokens retain their behavior.

ALTER TABLE tq_services
    MODIFY quota_code VARCHAR(64) NULL,
    ADD service_kind VARCHAR(16) NOT NULL DEFAULT 'atomic',
    ADD redemption_mode VARCHAR(16) NOT NULL DEFAULT 'instant',
    ADD charge_units BIGINT NULL,
    ADD session_ttl_seconds INTEGER NULL;

ALTER TABLE tq_assignments
    ADD term_start DATETIME(6) NULL,
    ADD term_end DATETIME(6) NULL;

UPDATE tq_assignments
SET term_start = effective_at, term_end = expires_at
WHERE term_start IS NULL;

ALTER TABLE tq_tokens
    ADD session_status VARCHAR(16) NULL,
    ADD session_expires_at DATETIME(6) NULL,
    ADD closed_at DATETIME(6) NULL;

CREATE TABLE tq_service_members (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id VARCHAR(64) NOT NULL,
    parent_service_code VARCHAR(64) NOT NULL,
    child_service_code VARCHAR(64) NOT NULL,
    max_uses INTEGER NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_tq_service_member UNIQUE (tenant_id, parent_service_code, child_service_code),
    FOREIGN KEY (tenant_id, parent_service_code)
        REFERENCES tq_services (tenant_id, service_code),
    FOREIGN KEY (tenant_id, child_service_code)
        REFERENCES tq_services (tenant_id, service_code)
);

CREATE TABLE tq_token_items (
    id BIGINT NOT NULL AUTO_INCREMENT,
    token_id BIGINT NOT NULL,
    child_service_code VARCHAR(64) NOT NULL,
    slot_no INTEGER NOT NULL,
    max_uses INTEGER NULL,
    request_key VARCHAR(128) NULL,
    status VARCHAR(16) NOT NULL,
    used_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_tq_token_item_slot UNIQUE (token_id, child_service_code, slot_no),
    CONSTRAINT uq_tq_token_item_request UNIQUE (token_id, request_key),
    FOREIGN KEY (token_id) REFERENCES tq_tokens (id)
);
