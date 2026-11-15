-- Tenant-owned quota policy, reservation and usage ledger.
BEGIN;

CREATE TABLE IF NOT EXISTS platform_tenant_quota_policies (
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id),
    dimension text NOT NULL CHECK (dimension IN ('concurrent_runs','tokens','tool_calls','storage_bytes','runtime_seconds')),
    quota_limit bigint NOT NULL CHECK (quota_limit > 0),
    window_seconds integer,
    hard_limit boolean NOT NULL DEFAULT true,
    warning_threshold numeric(5,4) NOT NULL DEFAULT 0.8000 CHECK (warning_threshold > 0 AND warning_threshold <= 1),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, dimension),
    CHECK ((dimension = 'concurrent_runs' AND window_seconds IS NULL) OR
           (dimension <> 'concurrent_runs' AND window_seconds > 0))
);

CREATE TABLE IF NOT EXISTS platform_quota_reservations (
    reservation_id uuid PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id),
    run_id uuid NOT NULL,
    dimension text NOT NULL CHECK (dimension IN ('concurrent_runs','tokens','tool_calls','storage_bytes','runtime_seconds')),
    window_start timestamptz NOT NULL,
    reserved_amount bigint NOT NULL CHECK (reserved_amount > 0),
    idempotency_key text NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','committed','released')),
    created_at timestamptz NOT NULL DEFAULT now(),
    settled_at timestamptz,
    UNIQUE (tenant_id, run_id, dimension, idempotency_key),
    FOREIGN KEY (tenant_id, run_id) REFERENCES platform_runs(tenant_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_platform_quota_reservations_active
    ON platform_quota_reservations(tenant_id, dimension, window_start)
    WHERE status = 'active';

CREATE TABLE IF NOT EXISTS platform_usage_events (
    event_id uuid PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id),
    run_id uuid NOT NULL,
    dimension text NOT NULL CHECK (dimension IN ('tokens','tool_calls','storage_bytes','runtime_seconds')),
    amount bigint NOT NULL CHECK (amount >= 0),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    idempotency_key text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (tenant_id, run_id, dimension, idempotency_key),
    FOREIGN KEY (tenant_id, run_id) REFERENCES platform_runs(tenant_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_platform_usage_events_tenant_time
    ON platform_usage_events(tenant_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_platform_usage_events_run
    ON platform_usage_events(tenant_id, run_id, dimension);

COMMIT;
