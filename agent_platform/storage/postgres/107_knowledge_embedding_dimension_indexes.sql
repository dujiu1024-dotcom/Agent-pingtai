-- Dual-dimension pgvector contract for versioned Knowledge embeddings.
-- Keep every historical migration filename and checksum unchanged.
BEGIN;

ALTER TABLE knowledge_embeddings
    ALTER COLUMN embedding TYPE vector
    USING embedding::vector;

UPDATE knowledge_embeddings
SET dimensions = vector_dims(embedding)
WHERE dimensions IS DISTINCT FROM vector_dims(embedding);

ALTER TABLE knowledge_embeddings
    DROP CONSTRAINT IF EXISTS knowledge_embeddings_dimensions_check;

ALTER TABLE knowledge_embeddings
    ADD CONSTRAINT knowledge_embeddings_dimensions_check
    CHECK (dimensions IN (1024, 1536));

ALTER TABLE knowledge_embeddings
    ADD CONSTRAINT knowledge_embeddings_vector_dimensions_check
    CHECK (vector_dims(embedding) = dimensions);

UPDATE knowledge_embedding_models
SET retired_at = NULL
WHERE dimensions IN (1024, 1536);

-- Model version is part of the immutable model@version storage namespace.
ALTER TABLE knowledge_embeddings
    DROP CONSTRAINT IF EXISTS knowledge_embeddings_pkey;

ALTER TABLE knowledge_embeddings
    ADD CONSTRAINT knowledge_embeddings_identity_key
    UNIQUE (tenant_id, chunk_id, embedding_model, model_version);

DROP INDEX IF EXISTS idx_knowledge_embeddings_cosine;
DROP INDEX IF EXISTS knowledge_embeddings_cosine_1024_idx;
DROP INDEX IF EXISTS knowledge_embeddings_cosine_1536_idx;

CREATE INDEX knowledge_embeddings_cosine_1024_idx
    ON knowledge_embeddings USING hnsw
    ((embedding::vector(1024)) vector_cosine_ops)
    WHERE dimensions = 1024;

CREATE INDEX knowledge_embeddings_cosine_1536_idx
    ON knowledge_embeddings USING hnsw
    ((embedding::vector(1536)) vector_cosine_ops)
    WHERE dimensions = 1536;

CREATE INDEX knowledge_embeddings_lookup_idx
    ON knowledge_embeddings
    (tenant_id, embedding_model, model_version, dimensions, chunk_id);

COMMIT;
