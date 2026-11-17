-- Knowledge collections and explicit index lifecycle metadata.
BEGIN;
CREATE TABLE IF NOT EXISTS knowledge_collections (
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id),
    collection_id uuid NOT NULL,
    name text NOT NULL CHECK (length(btrim(name)) > 0),
    description text NOT NULL DEFAULT '',
    project_id text,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    PRIMARY KEY (tenant_id, collection_id)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_collections_scope
    ON knowledge_collections (tenant_id, project_id, created_at DESC)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS knowledge_collection_documents (
    tenant_id text NOT NULL,
    collection_id uuid NOT NULL,
    document_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, collection_id, document_id),
    FOREIGN KEY (tenant_id, collection_id)
        REFERENCES knowledge_collections (tenant_id, collection_id) ON DELETE CASCADE,
    -- Document identity is tenant-scoped; lifecycle cleanup is coordinated by the repository.
);
CREATE INDEX IF NOT EXISTS idx_knowledge_collection_documents_document
    ON knowledge_collection_documents (tenant_id, document_id);

CREATE TABLE IF NOT EXISTS knowledge_indexes (
    tenant_id text NOT NULL,
    index_id uuid NOT NULL,
    collection_id uuid NOT NULL,
    index_name text NOT NULL,
    embedding_model text NOT NULL,
    dimensions integer NOT NULL CHECK (dimensions > 0),
    schema_version integer NOT NULL DEFAULT 1 CHECK (schema_version > 0),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'ready', 'failed', 'deleting')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, index_id),
    UNIQUE (tenant_id, collection_id, index_name),
    FOREIGN KEY (tenant_id, collection_id)
        REFERENCES knowledge_collections (tenant_id, collection_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_knowledge_indexes_lifecycle
    ON knowledge_indexes (tenant_id, collection_id, status);
COMMIT;
