-- Durable graph governance snapshots, audit trail and derived-data outbox.
CREATE TABLE IF NOT EXISTS platform_graph_governed_entities (
    tenant_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
    project_id TEXT,
    source_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    version INTEGER NOT NULL CHECK (version > 0),
    status TEXT NOT NULL CHECK (status IN ('active','archived','expired','deleted','superseded')),
    expires_at TIMESTAMPTZ,
    merged_into TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by TEXT NOT NULL,
    PRIMARY KEY (tenant_id, entity_id),
    FOREIGN KEY (tenant_id, merged_into)
      REFERENCES platform_graph_governed_entities (tenant_id, entity_id)
      DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX IF NOT EXISTS ix_graph_governed_expiry
    ON platform_graph_governed_entities (tenant_id, expires_at, entity_id)
    WHERE status='active' AND expires_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS platform_graph_governed_entity_history (
    tenant_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    entity_type TEXT NOT NULL,
    attributes JSONB NOT NULL,
    project_id TEXT,
    source_refs JSONB NOT NULL,
    status TEXT NOT NULL,
    expires_at TIMESTAMPTZ,
    merged_into TEXT,
    updated_at TIMESTAMPTZ NOT NULL,
    updated_by TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, entity_id, version)
);

CREATE TABLE IF NOT EXISTS platform_graph_governed_relations (
    tenant_id TEXT NOT NULL,
    relation_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    source_entity_id TEXT NOT NULL,
    target_entity_id TEXT NOT NULL,
    project_id TEXT,
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    version INTEGER NOT NULL CHECK (version > 0),
    status TEXT NOT NULL CHECK (status IN ('active','archived','expired','deleted','superseded')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, relation_id),
    FOREIGN KEY (tenant_id, source_entity_id)
      REFERENCES platform_graph_governed_entities (tenant_id, entity_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (tenant_id, target_entity_id)
      REFERENCES platform_graph_governed_entities (tenant_id, entity_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX IF NOT EXISTS ix_graph_governed_relation_source
    ON platform_graph_governed_relations (tenant_id, source_entity_id, relation_type);
CREATE INDEX IF NOT EXISTS ix_graph_governed_relation_target
    ON platform_graph_governed_relations (tenant_id, target_entity_id, relation_type);

CREATE TABLE IF NOT EXISTS platform_graph_governed_relation_history (
    tenant_id TEXT NOT NULL,
    relation_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    relation_type TEXT NOT NULL,
    source_entity_id TEXT NOT NULL,
    target_entity_id TEXT NOT NULL,
    project_id TEXT,
    attributes JSONB NOT NULL,
    source_refs JSONB NOT NULL,
    status TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, relation_id, version)
);

CREATE TABLE IF NOT EXISTS platform_graph_governance_audit (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    from_version INTEGER NOT NULL,
    to_version INTEGER NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (to_version >= from_version)
);
CREATE INDEX IF NOT EXISTS ix_graph_governance_audit_entity
    ON platform_graph_governance_audit (tenant_id, entity_id, occurred_at, event_id);

CREATE TABLE IF NOT EXISTS platform_graph_invalidation_jobs (
    job_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    object_kind TEXT NOT NULL,
    object_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
      CHECK (status IN ('pending','processing','completed','dead')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_owner TEXT,
    lease_until TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    UNIQUE (tenant_id, idempotency_key),
    CHECK ((status='processing') = (lease_owner IS NOT NULL AND lease_until IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ix_graph_invalidation_claim
    ON platform_graph_invalidation_jobs (tenant_id, status, available_at, job_id);

-- Governance history and audit are append-only evidence.
CREATE OR REPLACE FUNCTION reject_graph_governance_history_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'graph governance history and audit are append-only';
END;
$$;

DROP TRIGGER IF EXISTS trg_graph_entity_history_append_only
    ON platform_graph_governed_entity_history;
CREATE TRIGGER trg_graph_entity_history_append_only
    BEFORE UPDATE OR DELETE ON platform_graph_governed_entity_history
    FOR EACH ROW EXECUTE FUNCTION reject_graph_governance_history_mutation();

DROP TRIGGER IF EXISTS trg_graph_relation_history_append_only
    ON platform_graph_governed_relation_history;
CREATE TRIGGER trg_graph_relation_history_append_only
    BEFORE UPDATE OR DELETE ON platform_graph_governed_relation_history
    FOR EACH ROW EXECUTE FUNCTION reject_graph_governance_history_mutation();

DROP TRIGGER IF EXISTS trg_graph_governance_audit_append_only
    ON platform_graph_governance_audit;
CREATE TRIGGER trg_graph_governance_audit_append_only
    BEFORE UPDATE OR DELETE ON platform_graph_governance_audit
    FOR EACH ROW EXECUTE FUNCTION reject_graph_governance_history_mutation();
