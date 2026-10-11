-- One-time v0.10.x -> v0.11.0 migration: user IDs (`subject_id`) become case-sensitive strings.
-- Apply before starting 0.11.0. Existing integer IDs convert to their decimal strings (42 -> '42'),
-- which is exactly how 0.11.0 reads an integer `subject_id`, so members, usage and tokens carry
-- over unchanged. Indexes and unique constraints on these columns are kept.
-- Each statement rebuilds its table; run it in a low-traffic window.
-- Rolling back to 0.10.x is possible only until the first non-numeric `subject_id` is written.

ALTER TABLE tq_assignments MODIFY subject_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL;
ALTER TABLE tq_usage MODIFY subject_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL;
ALTER TABLE tq_subject_packages MODIFY subject_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL;
ALTER TABLE tq_tokens MODIFY subject_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL;
ALTER TABLE tq_ledger MODIFY subject_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL;
ALTER TABLE tq_package_grants MODIFY subject_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL;
