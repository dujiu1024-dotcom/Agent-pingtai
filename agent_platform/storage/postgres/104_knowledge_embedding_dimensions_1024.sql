-- Align persisted Knowledge embeddings and model metadata with the platform's
-- 1024-dimensional pgvector contract. Migration 098 rebuilds the vector column;
-- this follow-up also fixes the catalog defaults and retires incompatible profiles.
BEGIN;

ALTER TABLE knowledge_embeddings
    ALTER COLUMN embedding TYPE vector(1024)
    USING embedding::vector(1024);

ALTER TABLE knowledge_embeddings
    ALTER COLUMN dimensions SET DEFAULT 1024;

ALTER TABLE knowledge_embedding_models
    ALTER COLUMN dimensions SET DEFAULT 1024;

UPDATE knowledge_embedding_models
SET retired_at = COALESCE(retired_at, now())
WHERE dimensions <> 1024;

DO $$
DECLARE
    actual_type text;
BEGIN
    SELECT format_type(a.atttypid, a.atttypmod)
      INTO actual_type
      FROM pg_attribute a
     WHERE a.attrelid = 'knowledge_embeddings'::regclass
       AND a.attname = 'embedding'
       AND NOT a.attisdropped;

    IF actual_type <> 'vector(1024)' THEN
        RAISE EXCEPTION 'knowledge_embeddings.embedding must be vector(1024), got %', actual_type;
    END IF;
END
$$;

COMMIT;
