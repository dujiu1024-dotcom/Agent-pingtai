-- Knowledge graph extraction jobs: track document-to-graph extraction runs.
BEGIN;

CREATE TABLE IF NOT EXISTS platform_graph_extraction_jobs (
    tenant_id      text NOT NULL REFERENCES platform_tenants(tenant_id) ON DELETE CASCADE,
    job_id         text NOT NULL,
    status         text NOT NULL,
    document_ids   jsonb NOT NULL DEFAULT '[]'::jsonb,
    total          integer NOT NULL DEFAULT 0,
    processed      integer NOT NULL DEFAULT 0,
    entity_count   integer NOT NULL DEFAULT 0,
    relation_count integer NOT NULL DEFAULT 0,
    skipped        integer NOT NULL DEFAULT 0,
    error          text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, job_id),
    CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    CHECK (total >= 0 AND processed >= 0 AND entity_count >= 0 AND relation_count >= 0 AND skipped >= 0)
);

CREATE INDEX IF NOT EXISTS idx_graph_extraction_jobs_created
    ON platform_graph_extraction_jobs (tenant_id, created_at DESC);

COMMIT;
