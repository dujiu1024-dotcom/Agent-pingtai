-- Durable Run budget snapshots and per-model-call estimate/actual ledger.
BEGIN;
CREATE TABLE IF NOT EXISTS platform_run_budget_accounts (
    tenant_id text NOT NULL,
    run_id uuid NOT NULL,
    max_tokens bigint NOT NULL CHECK (max_tokens >= 0),
    max_cost_usd numeric(20,8) NOT NULL CHECK (max_cost_usd >= 0),
    reserved_tokens bigint NOT NULL DEFAULT 0 CHECK (reserved_tokens >= 0),
    reserved_cost_usd numeric(20,8) NOT NULL DEFAULT 0 CHECK (reserved_cost_usd >= 0),
    actual_tokens bigint NOT NULL DEFAULT 0 CHECK (actual_tokens >= 0),
    actual_cost_usd numeric(20,8) NOT NULL DEFAULT 0 CHECK (actual_cost_usd >= 0),
    overrun_reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,run_id),
    FOREIGN KEY (tenant_id,run_id) REFERENCES platform_runs(tenant_id,run_id)
);
CREATE TABLE IF NOT EXISTS platform_model_call_budget_ledger (
    call_id uuid PRIMARY KEY,
    tenant_id text NOT NULL,
    run_id uuid NOT NULL,
    model_key text NOT NULL,
    estimated_input_tokens bigint NOT NULL CHECK (estimated_input_tokens >= 0),
    estimated_output_tokens bigint NOT NULL CHECK (estimated_output_tokens >= 0),
    estimated_cost_usd numeric(20,8) NOT NULL CHECK (estimated_cost_usd >= 0),
    actual_input_tokens bigint,
    actual_output_tokens bigint,
    actual_cost_usd numeric(20,8),
    token_delta bigint,
    cost_delta_usd numeric(20,8),
    remaining_tokens bigint,
    remaining_cost_usd numeric(20,8),
    status text NOT NULL CHECK (status IN ('reserved','settled','rejected')),
    overrun_reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    settled_at timestamptz,
    FOREIGN KEY (tenant_id,run_id)
      REFERENCES platform_run_budget_accounts(tenant_id,run_id),
    CHECK ((status='settled') = (settled_at IS NOT NULL)),
    CHECK (status<>'settled' OR
      (actual_input_tokens IS NOT NULL AND actual_output_tokens IS NOT NULL
       AND actual_cost_usd IS NOT NULL AND token_delta IS NOT NULL
       AND cost_delta_usd IS NOT NULL AND remaining_tokens IS NOT NULL
       AND remaining_cost_usd IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_model_call_budget_run_time
    ON platform_model_call_budget_ledger(tenant_id,run_id,created_at,call_id);
COMMIT;
