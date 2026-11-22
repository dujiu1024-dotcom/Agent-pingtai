-- Enterprise AI value management: tenant-scoped baselines and auditable attribution.
CREATE TABLE IF NOT EXISTS ai_value_baselines (
    tenant_id TEXT NOT NULL,
    process_id TEXT NOT NULL,
    role_id TEXT NOT NULL,
    version TEXT NOT NULL,
    manual_minutes_per_unit NUMERIC(18,4) NOT NULL CHECK (manual_minutes_per_unit > 0),
    labor_cost_per_hour NUMERIC(18,4) NOT NULL CHECK (labor_cost_per_hour >= 0),
    quality_rate NUMERIC(8,6) NOT NULL CHECK (quality_rate BETWEEN 0 AND 1),
    sample_size INTEGER NOT NULL CHECK (sample_size > 0),
    source TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, process_id, role_id, version)
);
CREATE TABLE IF NOT EXISTS ai_value_attributions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    process_id TEXT NOT NULL,
    role_id TEXT NOT NULL,
    baseline_version TEXT NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL CHECK (window_end > window_start),
    units_total INTEGER NOT NULL CHECK (units_total > 0),
    auto_completed INTEGER NOT NULL CHECK (auto_completed >= 0),
    assisted_completed INTEGER NOT NULL CHECK (assisted_completed >= 0),
    human_takeovers INTEGER NOT NULL CHECK (human_takeovers >= 0),
    rework_units INTEGER NOT NULL CHECK (rework_units >= 0),
    failed_units INTEGER NOT NULL CHECK (failed_units >= 0),
    actual_human_minutes NUMERIC(18,4) NOT NULL CHECK (actual_human_minutes >= 0),
    rework_human_minutes NUMERIC(18,4) NOT NULL CHECK (rework_human_minutes >= 0),
    agent_run_minutes NUMERIC(18,4) NOT NULL DEFAULT 0 CHECK (agent_run_minutes >= 0),
    saved_minutes NUMERIC(18,4) NOT NULL CHECK (saved_minutes >= 0),
    labor_savings NUMERIC(18,4) NOT NULL CHECK (labor_savings >= 0),
    ai_cost NUMERIC(18,4),
    other_business_benefit NUMERIC(18,4),
    net_benefit NUMERIC(18,4),
    roi NUMERIC(18,8),
    financial_status TEXT NOT NULL CHECK (financial_status IN ('estimated','financially_confirmed')),
    cost_source TEXT,
    cost_basis TEXT NOT NULL DEFAULT 'estimated' CHECK (cost_basis IN ('estimated','settled')),
    business_evidence JSONB,
    attribution_version TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, process_id, role_id, baseline_version)
      REFERENCES ai_value_baselines (tenant_id, process_id, role_id, version),
    CHECK (auto_completed + assisted_completed + human_takeovers + failed_units <= units_total),
    CHECK (financial_status = 'estimated'
        OR (financial_status = 'financially_confirmed' AND cost_basis = 'settled'
            AND ai_cost IS NOT NULL AND other_business_benefit IS NOT NULL AND business_evidence IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ix_ai_value_attributions_tenant_window
    ON ai_value_attributions (tenant_id, window_start, window_end);
