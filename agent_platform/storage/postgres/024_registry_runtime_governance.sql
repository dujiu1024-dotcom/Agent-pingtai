BEGIN;

CREATE TABLE IF NOT EXISTS platform_mcp_health_checks (
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id) ON DELETE CASCADE,
    server_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('healthy', 'unhealthy')),
    checked_at timestamptz NOT NULL,
    latency_ms integer NOT NULL CHECK (latency_ms >= 0),
    expected_tools jsonb NOT NULL DEFAULT '[]'::jsonb,
    discovered_tools jsonb NOT NULL DEFAULT '[]'::jsonb,
    error_code text,
    PRIMARY KEY (tenant_id, server_id)
);

CREATE INDEX IF NOT EXISTS idx_platform_mcp_health_status
    ON platform_mcp_health_checks (tenant_id, status, checked_at DESC);

CREATE TABLE IF NOT EXISTS platform_skill_dependency_reviews (
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id) ON DELETE CASCADE,
    skill_id text NOT NULL,
    skill_version integer NOT NULL CHECK (skill_version > 0),
    manifest_digest text NOT NULL CHECK (manifest_digest ~ '^[0-9a-f]{64}$'),
    status text NOT NULL CHECK (status IN ('approved', 'rejected')),
    dependencies jsonb NOT NULL DEFAULT '{}'::jsonb,
    findings jsonb NOT NULL DEFAULT '[]'::jsonb,
    reviewed_by text NOT NULL,
    reviewed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, skill_id, skill_version)
);

CREATE INDEX IF NOT EXISTS idx_platform_skill_review_status
    ON platform_skill_dependency_reviews (tenant_id, status, reviewed_at DESC);

CREATE TABLE IF NOT EXISTS platform_model_usage_events (
    usage_event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id) ON DELETE CASCADE,
    run_id text NOT NULL,
    request_id text NOT NULL,
    template_id text NOT NULL,
    template_version integer NOT NULL CHECK (template_version > 0),
    provider_id text NOT NULL,
    provider_version integer NOT NULL CHECK (provider_version > 0),
    model_id text NOT NULL,
    usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    estimated_cost_micros bigint CHECK (estimated_cost_micros IS NULL OR estimated_cost_micros >= 0),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, run_id, request_id)
);

CREATE INDEX IF NOT EXISTS idx_platform_model_usage_run
    ON platform_model_usage_events (tenant_id, run_id, recorded_at DESC);

COMMIT;
