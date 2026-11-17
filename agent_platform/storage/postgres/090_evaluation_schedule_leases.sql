BEGIN;
CREATE TABLE IF NOT EXISTS platform_evaluation_schedule_leases (
    tenant_id text NOT NULL,
    schedule_id text NOT NULL,
    schedule_fingerprint char(64) NOT NULL,
    next_run_at timestamptz NOT NULL DEFAULT '-infinity',
    lease_owner text,
    lease_expires_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,schedule_id),
    CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_platform_evaluation_schedule_due
    ON platform_evaluation_schedule_leases (next_run_at,lease_expires_at);
COMMENT ON TABLE platform_evaluation_schedule_leases IS
    'Durable tenant-scoped scheduler leases and next-run fences for Agent/RAG evaluations';
COMMIT;
