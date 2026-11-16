-- Fix NULL aggregation when there are no active reservations.
-- Without COALESCE, the first reservation could bypass hard token/cost limits.
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
