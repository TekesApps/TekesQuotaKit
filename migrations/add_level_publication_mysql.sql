ALTER TABLE tq_levels ADD COLUMN publication_status VARCHAR(16) NOT NULL DEFAULT 'test';
UPDATE tq_levels SET publication_status = 'formal';
