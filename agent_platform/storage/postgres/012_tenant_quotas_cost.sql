-- Tenant-scoped budgets, atomic Run reservations and append-only actual usage ledger.
BEGIN;
CREATE TABLE IF NOT EXISTS platform_quota_budgets (
    tenant_id text NOT NULL,
    scope_type text NOT NULL CHECK (scope_type IN ('tenant','project','agent','model')),
    scope_id text NOT NULL,
    period_start timestamptz NOT NULL,
    period_end timestamptz NOT NULL CHECK (period_end > period_start),
    hard_tokens bigint CHECK (hard_tokens IS NULL OR hard_tokens >= 0),
    hard_cost_usd numeric(20,8) CHECK (hard_cost_usd IS NULL OR hard_cost_usd >= 0),
    hard_concurrency integer CHECK (hard_concurrency IS NULL OR hard_concurrency >= 0),
    soft_tokens bigint CHECK (soft_tokens IS NULL OR soft_tokens >= 0),
    soft_cost_usd numeric(20,8) CHECK (soft_cost_usd IS NULL OR soft_cost_usd >= 0),
    priority integer NOT NULL DEFAULT 100,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, scope_type, scope_id, period_start)
);
CREATE TABLE IF NOT EXISTS platform_quota_reservations (
    reservation_id uuid PRIMARY KEY,
    tenant_id text NOT NULL,
    run_id uuid NOT NULL,
    scope_type text NOT NULL,
    scope_id text NOT NULL,
    period_start timestamptz NOT NULL,
    reserved_tokens bigint NOT NULL CHECK (reserved_tokens >= 0),
    reserved_cost_usd numeric(20,8) NOT NULL CHECK (reserved_cost_usd >= 0),
    status text NOT NULL CHECK (status IN ('active','settled','released','expired')),
    created_at timestamptz NOT NULL DEFAULT now(),
    settled_at timestamptz,
    FOREIGN KEY (tenant_id, run_id) REFERENCES platform_runs(tenant_id, run_id),
    FOREIGN KEY (tenant_id, scope_type, scope_id, period_start)
        REFERENCES platform_quota_budgets(tenant_id, scope_type, scope_id, period_start)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX IF NOT EXISTS idx_platform_quota_reservations_active
    ON platform_quota_reservations(tenant_id, scope_type, scope_id, created_at)
    WHERE status = 'active';
CREATE TABLE IF NOT EXISTS platform_usage_ledger (
    event_id uuid PRIMARY KEY,
    tenant_id text NOT NULL,
    run_id uuid NOT NULL,
    model_key text NOT NULL,
    project_id text,
    agent_id text,
    task_id text,
    input_tokens bigint NOT NULL CHECK (input_tokens >= 0),
    output_tokens bigint NOT NULL CHECK (output_tokens >= 0),
    tool_calls integer NOT NULL DEFAULT 0 CHECK (tool_calls >= 0),
    runtime_ms bigint NOT NULL DEFAULT 0 CHECK (runtime_ms >= 0),
    storage_bytes bigint NOT NULL DEFAULT 0 CHECK (storage_bytes >= 0),
    cost_usd numeric(20,8) NOT NULL CHECK (cost_usd >= 0),
    price_card_version text,
    source text NOT NULL CHECK (source IN ('estimated','provider_reported','reconciled')),
    occurred_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, run_id) REFERENCES platform_runs(tenant_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_platform_usage_tenant_time
    ON platform_usage_ledger(tenant_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_platform_usage_run_time
    ON platform_usage_ledger(tenant_id, run_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_platform_usage_model_time
    ON platform_usage_ledger(tenant_id, model_key, occurred_at DESC);
CREATE TABLE IF NOT EXISTS platform_usage_reconciliation (
    reconciliation_id uuid PRIMARY KEY,
    tenant_id text NOT NULL,
    period_start timestamptz NOT NULL,
    period_end timestamptz NOT NULL CHECK (period_end > period_start),
    source text NOT NULL,
    estimated_cost_usd numeric(20,8) NOT NULL,
    actual_cost_usd numeric(20,8) NOT NULL,
    variance_usd numeric(20,8) GENERATED ALWAYS AS (actual_cost_usd - estimated_cost_usd) STORED,
    imported_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, period_start, period_end, source)
);

-- Enforce append-only usage records for the runtime role; owner-only migrations may manage schema.
CREATE OR REPLACE FUNCTION platform_reject_usage_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'platform_usage_ledger is append-only';
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_platform_usage_no_update_delete ON platform_usage_ledger;
CREATE TRIGGER trg_platform_usage_no_update_delete
    BEFORE UPDATE OR DELETE ON platform_usage_ledger
    FOR EACH ROW EXECUTE FUNCTION platform_reject_usage_mutation();
REVOKE UPDATE, DELETE, TRUNCATE ON platform_usage_ledger FROM PUBLIC;


-- Reserve under a row lock: concurrent Workers serialize on the exact budget row.
CREATE OR REPLACE FUNCTION platform_quota_reserve(
    p_reservation_id uuid, p_tenant_id text, p_run_id uuid,
    p_scope_type text, p_scope_id text, p_period_start timestamptz,
    p_reserved_tokens bigint, p_reserved_cost_usd numeric
) RETURNS TABLE(reservation_id uuid, warning_soft_tokens boolean, warning_soft_cost boolean)
LANGUAGE plpgsql AS $quota$
DECLARE
    b platform_quota_budgets%ROWTYPE;
    used_tokens bigint;
    used_cost numeric(20,8);
    active_runs integer;
BEGIN
    IF p_reserved_tokens < 0 OR p_reserved_cost_usd < 0 THEN
        RAISE EXCEPTION 'reservation estimates must be non-negative';
    END IF;
    SELECT * INTO b FROM platform_quota_budgets
     WHERE tenant_id=p_tenant_id AND scope_type=p_scope_type
       AND scope_id=p_scope_id AND period_start=p_period_start
     FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'quota budget not configured'; END IF;

    SELECT COALESCE(SUM(input_tokens + output_tokens),0), COALESCE(SUM(cost_usd),0)
      INTO used_tokens, used_cost
      FROM platform_usage_ledger
     WHERE tenant_id=p_tenant_id AND occurred_at >= b.period_start AND occurred_at < b.period_end
       AND ((p_scope_type='tenant' AND tenant_id=p_scope_id)
         OR (p_scope_type='model' AND model_key=p_scope_id)
         OR (p_scope_type='project' AND project_id=p_scope_id)
         OR (p_scope_type='agent' AND agent_id=p_scope_id));
    SELECT COUNT(*) INTO active_runs FROM platform_quota_reservations
     WHERE tenant_id=p_tenant_id AND scope_type=p_scope_type AND scope_id=p_scope_id
       AND period_start=p_period_start AND status='active';

    SELECT used_tokens + COALESCE(SUM(reserved_tokens),0),
           used_cost + COALESCE(SUM(reserved_cost_usd),0)
      INTO used_tokens, used_cost
      FROM platform_quota_reservations
     WHERE tenant_id=p_tenant_id AND scope_type=p_scope_type AND scope_id=p_scope_id
       AND period_start=p_period_start AND status='active';

    IF b.hard_tokens IS NOT NULL AND used_tokens + p_reserved_tokens > b.hard_tokens
       THEN RAISE EXCEPTION 'hard token budget exceeded'; END IF;
    IF b.hard_cost_usd IS NOT NULL AND used_cost + p_reserved_cost_usd > b.hard_cost_usd
       THEN RAISE EXCEPTION 'hard cost budget exceeded'; END IF;
    IF b.hard_concurrency IS NOT NULL AND active_runs + 1 > b.hard_concurrency
       THEN RAISE EXCEPTION 'concurrency quota exceeded'; END IF;

    INSERT INTO platform_quota_reservations(
        reservation_id,tenant_id,run_id,scope_type,scope_id,period_start,
        reserved_tokens,reserved_cost_usd,status
    ) VALUES (
        p_reservation_id,p_tenant_id,p_run_id,p_scope_type,p_scope_id,p_period_start,
        p_reserved_tokens,p_reserved_cost_usd,'active'
    );
    RETURN QUERY SELECT p_reservation_id,
        b.soft_tokens IS NOT NULL AND used_tokens + p_reserved_tokens > b.soft_tokens,
        b.soft_cost_usd IS NOT NULL AND used_cost + p_reserved_cost_usd > b.soft_cost_usd;
END;
$quota$;

CREATE OR REPLACE FUNCTION platform_quota_release(p_reservation_id uuid) RETURNS boolean
LANGUAGE plpgsql AS $quota$
DECLARE changed integer;
BEGIN
    UPDATE platform_quota_reservations SET status='released', settled_at=now()
     WHERE reservation_id=p_reservation_id AND status='active';
    GET DIAGNOSTICS changed = ROW_COUNT;
    RETURN changed = 1;
END;
$quota$;

COMMIT;
