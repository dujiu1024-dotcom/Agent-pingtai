-- Tenant-scoped relational graph store and durable asynchronous index queue.
BEGIN;

CREATE TABLE IF NOT EXISTS platform_graph_entities (
    tenant_id text NOT NULL,
    entity_id text NOT NULL,
    entity_type text NOT NULL,
    project_id text,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_domain text NOT NULL CHECK (source_domain IN ('knowledge','memory','blackboard')),
    source_type text NOT NULL,
    source_id text NOT NULL,
    source_version integer NOT NULL CHECK (source_version > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, entity_id),
    UNIQUE (tenant_id, source_domain, source_type, source_id, source_version),
    CHECK (jsonb_typeof(attributes) = 'object'),
    CHECK (length(tenant_id) BETWEEN 1 AND 128),
    CHECK (length(entity_id) BETWEEN 1 AND 200),
    CHECK (length(entity_type) BETWEEN 1 AND 63),
    CHECK (length(source_type) BETWEEN 1 AND 63),
    CHECK (length(source_id) BETWEEN 1 AND 200)
);

CREATE INDEX IF NOT EXISTS platform_graph_entities_tenant_type_idx
    ON platform_graph_entities (tenant_id, entity_type, updated_at DESC);
CREATE INDEX IF NOT EXISTS platform_graph_entities_tenant_project_type_idx
    ON platform_graph_entities (tenant_id, project_id, entity_type);
CREATE INDEX IF NOT EXISTS platform_graph_entities_source_ref_idx
    ON platform_graph_entities (tenant_id, source_domain, source_type, source_id, source_version);
CREATE INDEX IF NOT EXISTS platform_graph_entities_title_idx
    ON platform_graph_entities (tenant_id, lower(attributes->>'title'))
    WHERE attributes ? 'title';

CREATE TABLE IF NOT EXISTS platform_graph_relations (
    tenant_id text NOT NULL,
    relation_id text NOT NULL,
    relation_type text NOT NULL,
    project_id text,
    source_entity_id text NOT NULL,
    target_entity_id text NOT NULL,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, relation_id),
    FOREIGN KEY (tenant_id, source_entity_id)
        REFERENCES platform_graph_entities (tenant_id, entity_id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, target_entity_id)
        REFERENCES platform_graph_entities (tenant_id, entity_id) ON DELETE CASCADE,
    CHECK (jsonb_typeof(attributes) = 'object'),
    CHECK (length(relation_id) BETWEEN 1 AND 200),
    CHECK (length(relation_type) BETWEEN 1 AND 63)
);

CREATE INDEX IF NOT EXISTS platform_graph_relations_source_idx
    ON platform_graph_relations (tenant_id, source_entity_id, relation_type);
CREATE INDEX IF NOT EXISTS platform_graph_relations_target_idx
    ON platform_graph_relations (tenant_id, target_entity_id, relation_type);
CREATE INDEX IF NOT EXISTS platform_graph_relations_project_type_idx
    ON platform_graph_relations (tenant_id, project_id, relation_type);

CREATE TABLE IF NOT EXISTS platform_graph_index_jobs (
    job_id bigserial PRIMARY KEY,
    tenant_id text NOT NULL,
    job_key text NOT NULL,
    object_kind text NOT NULL CHECK (object_kind IN ('entity','relation')),
    object_id text NOT NULL,
    operation text NOT NULL CHECK (operation IN ('upsert','delete')),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','processing','completed','dead')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_owner text,
    lease_until timestamptz,
    last_error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    UNIQUE (tenant_id, job_key),
    CHECK (jsonb_typeof(payload) = 'object'),
    CHECK (
        (status = 'processing' AND lease_owner IS NOT NULL AND lease_until IS NOT NULL)
        OR (status <> 'processing' AND lease_owner IS NULL AND lease_until IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS platform_graph_index_jobs_claim_idx
    ON platform_graph_index_jobs (available_at, job_id)
    WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS platform_graph_index_jobs_expired_lease_idx
    ON platform_graph_index_jobs (lease_until, job_id)
    WHERE status = 'processing';
CREATE INDEX IF NOT EXISTS platform_graph_index_jobs_tenant_status_idx
    ON platform_graph_index_jobs (tenant_id, status, updated_at DESC);

COMMIT;
