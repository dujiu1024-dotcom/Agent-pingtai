-- Knowledge collection and index catalog.
-- Safe to apply repeatedly; existing documents remain in the implicit default namespace.
CREATE TABLE IF NOT EXISTS knowledge_collections (
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id) ON DELETE CASCADE,
    collection_id uuid NOT NULL,
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 200),
    description text NOT NULL DEFAULT '',
    project_id text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    PRIMARY KEY (tenant_id, collection_id),
    UNIQUE (tenant_id, project_id, name)
);

ALTER TABLE knowledge_collections
    ADD COLUMN IF NOT EXISTS created_by text NOT NULL DEFAULT 'platform-system';

CREATE UNIQUE INDEX IF NOT EXISTS knowledge_collections_scoped_name_uidx
    ON knowledge_collections (tenant_id, COALESCE(project_id, ''), lower(name))
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS knowledge_collections_tenant_project_idx
    ON knowledge_collections (tenant_id, project_id)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS knowledge_indexes (
    tenant_id text NOT NULL,
    index_id uuid NOT NULL,
    collection_id uuid NOT NULL,
    name text NOT NULL DEFAULT 'default',
    index_type text NOT NULL CHECK (index_type IN ('lexical', 'vector', 'hybrid')),
    embedding_model text,
    dimensions integer CHECK (dimensions IS NULL OR dimensions > 0),
    status text NOT NULL DEFAULT 'ready'
        CHECK (status IN ('provisioning', 'ready', 'degraded', 'failed', 'disabled')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, index_id),
    UNIQUE (tenant_id, collection_id, name),
    FOREIGN KEY (tenant_id, collection_id)
        REFERENCES knowledge_collections(tenant_id, collection_id) ON DELETE CASCADE
);

ALTER TABLE knowledge_documents
    ADD COLUMN IF NOT EXISTS collection_id uuid;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'knowledge_documents_collection_fk'
    ) THEN
        ALTER TABLE knowledge_documents
            ADD CONSTRAINT knowledge_documents_collection_fk
            FOREIGN KEY (tenant_id, collection_id)
            REFERENCES knowledge_collections(tenant_id, collection_id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS knowledge_documents_collection_version_idx
    ON knowledge_documents (tenant_id, collection_id, document_id, version DESC)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS knowledge_chunks_document_version_idx
    ON knowledge_chunks (tenant_id, document_id, document_version, ordinal);
