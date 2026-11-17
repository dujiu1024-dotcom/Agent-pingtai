-- Align the original catalog schema with the collection repository contract.
-- The early catalog migration used index_name/created_by and omitted metadata,
-- while the current repository uses name/index_type and JSON metadata.
BEGIN;

ALTER TABLE knowledge_collections
    ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE knowledge_collections
    ALTER COLUMN created_by SET DEFAULT '';

ALTER TABLE knowledge_indexes
    ADD COLUMN IF NOT EXISTS name text;
ALTER TABLE knowledge_indexes
    ADD COLUMN IF NOT EXISTS index_type text NOT NULL DEFAULT 'hybrid';

DO $
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema=current_schema() AND table_name='knowledge_indexes'
          AND column_name='index_name'
    ) THEN
        EXECUTE 'UPDATE knowledge_indexes SET name=index_name WHERE name IS NULL';
        EXECUTE 'ALTER TABLE knowledge_indexes ALTER COLUMN index_name DROP NOT NULL';
    END IF;
END $;

UPDATE knowledge_indexes SET name='default' WHERE name IS NULL;
ALTER TABLE knowledge_indexes ALTER COLUMN name SET NOT NULL;
ALTER TABLE knowledge_indexes ALTER COLUMN embedding_model DROP NOT NULL;
ALTER TABLE knowledge_indexes ALTER COLUMN dimensions DROP NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS knowledge_indexes_collection_name_uidx
    ON knowledge_indexes (tenant_id, collection_id, name);

COMMIT;
