-- Durable audit evidence for migration and data-governance operations.
CREATE TABLE IF NOT EXISTS platform_data_lifecycle_audit (
    event_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL CHECK (length(btrim(tenant_id)) BETWEEN 1 AND 256),
    actor_id TEXT NOT NULL CHECK (length(btrim(actor_id)) BETWEEN 1 AND 256),
    action TEXT NOT NULL CHECK (action ~ '^[a-z][a-z0-9_.-]{1,127}$'),
    outcome TEXT NOT NULL CHECK (outcome IN ('started','succeeded','failed','denied','retry_scheduled')),
    scope JSONB NOT NULL CHECK (jsonb_typeof(scope) = 'object' AND scope <> '{}'::jsonb),
    correlation_id UUID NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    failure_code TEXT,
    evidence_sha256 CHAR(64),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((outcome = 'failed' AND failure_code ~ '^[A-Z][A-Z0-9_.-]{1,63}$')
        OR (outcome <> 'failed' AND failure_code IS NULL)),
    CHECK (evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS platform_data_lifecycle_audit_tenant_time_idx
    ON platform_data_lifecycle_audit (tenant_id, occurred_at DESC, event_id DESC);
CREATE INDEX IF NOT EXISTS platform_data_lifecycle_audit_correlation_idx
    ON platform_data_lifecycle_audit (tenant_id, correlation_id, occurred_at DESC);

CREATE OR REPLACE FUNCTION reject_data_lifecycle_audit_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'platform_data_lifecycle_audit is append-only';
END;
$$;

DROP TRIGGER IF EXISTS platform_data_lifecycle_audit_no_update_delete
    ON platform_data_lifecycle_audit;
CREATE TRIGGER platform_data_lifecycle_audit_no_update_delete
    BEFORE UPDATE OR DELETE ON platform_data_lifecycle_audit
    FOR EACH ROW EXECUTE FUNCTION reject_data_lifecycle_audit_mutation();

DROP TRIGGER IF EXISTS platform_data_lifecycle_audit_no_truncate
    ON platform_data_lifecycle_audit;
CREATE TRIGGER platform_data_lifecycle_audit_no_truncate
    BEFORE TRUNCATE ON platform_data_lifecycle_audit
    FOR EACH STATEMENT EXECUTE FUNCTION reject_data_lifecycle_audit_mutation();

-- Deployment must grant the application role INSERT/SELECT only; do not grant
-- UPDATE, DELETE, TRUNCATE, or DDL. Tenant filtering is enforced by the repository.
