-- L2-T02 business metrics and auditable links to the existing #17 usage ledger.
CREATE TABLE IF NOT EXISTS ai_value_metric_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    attribution_id UUID NOT NULL REFERENCES ai_value_attributions(id) ON DELETE CASCADE,
    process_id TEXT NOT NULL,
    role_id TEXT NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL CHECK (window_end > window_start),
    completed_units INTEGER NOT NULL CHECK (completed_units >= 0),
    baseline_cycle_minutes_per_unit NUMERIC(18,4) NOT NULL CHECK (baseline_cycle_minutes_per_unit > 0),
    actual_cycle_minutes_per_unit NUMERIC(18,4) NOT NULL CHECK (actual_cycle_minutes_per_unit >= 0),
    cycle_time_delta_minutes_per_unit NUMERIC(18,4) NOT NULL,
    cycle_time_reduction_rate NUMERIC(18,8) NOT NULL,
    cycle_minutes_saved NUMERIC(18,4) NOT NULL,
    baseline_throughput_per_hour NUMERIC(18,4) NOT NULL CHECK (baseline_throughput_per_hour > 0),
    actual_throughput_per_hour NUMERIC(18,4) NOT NULL CHECK (actual_throughput_per_hour >= 0),
    throughput_delta_per_hour NUMERIC(18,4) NOT NULL,
    throughput_change_rate NUMERIC(18,8) NOT NULL,
    run_ids TEXT[] NOT NULL CHECK (cardinality(run_ids) > 0),
    cost_breakdown JSONB NOT NULL,
    metric_version TEXT NOT NULL,
    source_metadata JSONB NOT NULL,
    actor_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, attribution_id, metric_version)
);
CREATE INDEX IF NOT EXISTS ix_ai_value_metric_snapshots_tenant_window
    ON ai_value_metric_snapshots (tenant_id, window_start, window_end);
CREATE INDEX IF NOT EXISTS ix_ai_value_metric_snapshots_run_ids
    ON ai_value_metric_snapshots USING GIN (run_ids);
