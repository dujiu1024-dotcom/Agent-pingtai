-- Chunk-level ACLs and durable invalidation events for derived knowledge caches.
BEGIN;
ALTER TABLE knowledge_chunks
    ADD COLUMN IF NOT EXISTS acl_enforced boolean NOT NULL DEFAULT false;
CREATE TABLE IF NOT EXISTS knowledge_chunk_acl (
    tenant_id text NOT NULL,
    chunk_id uuid NOT NULL,
    principal_type text NOT NULL CHECK (principal_type IN ('user','role','project')),
    principal_id text NOT NULL CHECK (length(btrim(principal_id)) > 0),
    permission text NOT NULL CHECK (permission IN ('read','manage')),
    granted_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, chunk_id, principal_type, principal_id, permission)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunk_acl_principal
    ON knowledge_chunk_acl (tenant_id, principal_type, principal_id, chunk_id);
CREATE TABLE IF NOT EXISTS knowledge_cache_invalidations (
    event_id bigserial PRIMARY KEY,
    tenant_id text NOT NULL,
    resource_type text NOT NULL CHECK (resource_type IN ('document','chunk')),
    resource_id uuid NOT NULL,
    reason text NOT NULL CHECK (reason IN ('acl_changed','deleted','updated')),
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_knowledge_cache_invalidations_pending
    ON knowledge_cache_invalidations (event_id) WHERE processed_at IS NULL;
COMMIT;
