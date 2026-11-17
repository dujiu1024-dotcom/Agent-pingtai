BEGIN;

CREATE TABLE IF NOT EXISTS platform_data_lifecycle_audit (
    event_id uuid PRIMARY KEY,
    tenant_id text NOT NULL,
    actor_id text NOT NULL,
    action text NOT NULL CHECK (action ~ '^[a-z][a-z0-9_.-]{1,127}$'),
    outcome text NOT NULL CHECK (outcome IN ('started','succeeded','failed','denied','retry_scheduled')),
    scope jsonb NOT NULL CHECK (jsonb_typeof(scope) = 'object' AND scope <> '{}'::jsonb),
    correlation_id uuid NOT NULL,
    attempt integer NOT NULL CHECK (attempt >= 1),
    failure_code text CHECK (failure_code IS NULL OR failure_code ~ '^[A-Z][A-Z0-9_.-]{1,63}$'),
    evidence_sha256 char(64) CHECK (evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$'),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((outcome = 'failed' AND failure_code IS NOT NULL)
        OR (outcome <> 'failed' AND failure_code IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_data_lifecycle_audit_tenant_time
    ON platform_data_lifecycle_audit (tenant_id, occurred_at DESC, event_id DESC);
CREATE INDEX IF NOT EXISTS idx_data_lifecycle_audit_correlation
    ON platform_data_lifecycle_audit (tenant_id, correlation_id, attempt);

CREATE OR REPLACE FUNCTION reject_data_lifecycle_audit_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'platform_data_lifecycle_audit is append-only';
END;
$$;

DROP TRIGGER IF EXISTS trg_data_lifecycle_audit_immutable ON platform_data_lifecycle_audit;
CREATE TRIGGER trg_data_lifecycle_audit_immutable
    BEFORE UPDATE OR DELETE ON platform_data_lifecycle_audit
    FOR EACH ROW EXECUTE FUNCTION reject_data_lifecycle_audit_mutation();

COMMENT ON TABLE platform_data_lifecycle_audit IS
    'Tenant-scoped append-only evidence for migration and data lifecycle governance. Evidence is represented by SHA-256 digest only; raw payloads must not be stored.';

COMMIT;
