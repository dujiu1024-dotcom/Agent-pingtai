-- Versioned embedding catalog; model@version is the immutable storage namespace.
BEGIN;
CREATE TABLE IF NOT EXISTS knowledge_embedding_models (
    embedding_model text NOT NULL,
    model_version text NOT NULL,
    dimensions integer NOT NULL CHECK (dimensions > 0),
    provider text NOT NULL DEFAULT 'custom',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    retired_at timestamptz,
    PRIMARY KEY (embedding_model, model_version)
);
ALTER TABLE knowledge_embeddings
    ADD COLUMN IF NOT EXISTS model_version text NOT NULL DEFAULT '1';
ALTER TABLE knowledge_embeddings
    ADD COLUMN IF NOT EXISTS dimensions integer NOT NULL DEFAULT 1536 CHECK (dimensions > 0);
CREATE INDEX IF NOT EXISTS idx_knowledge_embeddings_model_version
    ON knowledge_embeddings (tenant_id, embedding_model, model_version, chunk_id);
COMMIT;
