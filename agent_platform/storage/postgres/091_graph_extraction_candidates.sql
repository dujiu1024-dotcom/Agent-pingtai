-- Durable, tenant-isolated review queue for extracted graph candidates.
BEGIN;

CREATE TABLE IF NOT EXISTS platform_graph_extraction_candidates (
    tenant_id       text NOT NULL REFERENCES platform_tenants(tenant_id) ON DELETE CASCADE,
    candidate_id    char(64) NOT NULL,
    source_domain   text NOT NULL CHECK (source_domain IN ('knowledge', 'memory', 'blackboard')),
    source_type     text NOT NULL,
    source_id       text NOT NULL,
    source_version  text NOT NULL,
    source_ref      text NOT NULL,
    project_id      text,
    candidate_kind  text NOT NULL CHECK (candidate_kind IN ('entity', 'relation')),
    local_id        text NOT NULL,
    payload         jsonb NOT NULL,
    confidence      double precision NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    evidence_refs   jsonb NOT NULL,
    conflict_key    text,
    status          text NOT NULL DEFAULT 'candidate'
                    CHECK (status IN ('candidate', 'conflict', 'accepted', 'rejected')),
    reviewed_by     text,
    review_reason   text,
    reviewed_at     timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, candidate_id),
    CHECK (length(candidate_id) = 64),
    CHECK (jsonb_typeof(payload) = 'object'),
    CHECK (jsonb_typeof(evidence_refs) = 'array'),
    CHECK ((status IN ('candidate', 'conflict') AND reviewed_by IS NULL
            AND reviewed_at IS NULL AND review_reason IS NULL)
        OR (status IN ('accepted', 'rejected') AND reviewed_by IS NOT NULL
            AND reviewed_at IS NOT NULL AND review_reason IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_graph_candidates_review
    ON platform_graph_extraction_candidates (tenant_id, status, created_at, candidate_id);
CREATE INDEX IF NOT EXISTS idx_graph_candidates_source
    ON platform_graph_extraction_candidates
       (tenant_id, source_domain, source_type, source_id, source_version);
CREATE INDEX IF NOT EXISTS idx_graph_candidates_conflict
    ON platform_graph_extraction_candidates (tenant_id, conflict_key)
    WHERE conflict_key IS NOT NULL;

COMMIT;
