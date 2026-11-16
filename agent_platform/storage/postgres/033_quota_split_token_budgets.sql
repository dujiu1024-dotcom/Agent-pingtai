-- Independent input/output token ceilings with serialized reservation checks.
BEGIN;
ALTER TABLE platform_quota_budgets
    ADD COLUMN IF NOT EXISTS hard_input_tokens bigint
        CHECK (hard_input_tokens IS NULL OR hard_input_tokens >= 0),
    ADD COLUMN IF NOT EXISTS hard_output_tokens bigint
        CHECK (hard_output_tokens IS NULL OR hard_output_tokens >= 0);
ALTER TABLE platform_quota_reservations
    ADD COLUMN IF NOT EXISTS reserved_input_tokens bigint NOT NULL DEFAULT 0
        CHECK (reserved_input_tokens >= 0),
    ADD COLUMN IF NOT EXISTS reserved_output_tokens bigint NOT NULL DEFAULT 0
        CHECK (reserved_output_tokens >= 0);

-- Historical aggregate reservations are attributed to input conservatively.
UPDATE platform_quota_reservations
   SET reserved_input_tokens=reserved_tokens
 WHERE reserved_input_tokens=0 AND reserved_output_tokens=0 AND reserved_tokens>0;

CREATE OR REPLACE FUNCTION platform_quota_reserve_split(
    p_reservation_id uuid, p_tenant_id text, p_run_id uuid,
    p_scope_type text, p_scope_id text, p_period_start timestamptz,
    p_reserved_tokens bigint, p_reserved_cost_usd numeric,
    p_reserved_input_tokens bigint, p_reserved_output_tokens bigint
) RETURNS TABLE(warning_soft_tokens boolean, warning_soft_cost boolean)
LANGUAGE plpgsql AS $quota$
DECLARE
    b platform_quota_budgets%ROWTYPE;
    used_input bigint;
    used_output bigint;
    soft_token_warning boolean;
    soft_cost_warning boolean;
BEGIN
    IF p_reserved_input_tokens < 0 OR p_reserved_output_tokens < 0
       OR p_reserved_input_tokens + p_reserved_output_tokens <> p_reserved_tokens THEN
        RAISE EXCEPTION 'input/output estimates must be non-negative and sum to total tokens';
    END IF;
    SELECT * INTO b FROM platform_quota_budgets
     WHERE tenant_id=p_tenant_id AND scope_type=p_scope_type
       AND scope_id=p_scope_id AND period_start=p_period_start
     FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'quota budget not configured'; END IF;

    SELECT COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0)
      INTO used_input, used_output
      FROM platform_usage_ledger
     WHERE tenant_id=p_tenant_id AND occurred_at >= b.period_start AND occurred_at < b.period_end
       AND ((p_scope_type='tenant' AND tenant_id=p_scope_id)
         OR (p_scope_type='model' AND model_key=p_scope_id)
         OR (p_scope_type='project' AND project_id=p_scope_id)
         OR (p_scope_type='agent' AND agent_id=p_scope_id));
    SELECT used_input + COALESCE(SUM(reserved_input_tokens),0),
           used_output + COALESCE(SUM(reserved_output_tokens),0)
      INTO used_input, used_output
      FROM platform_quota_reservations
     WHERE tenant_id=p_tenant_id AND scope_type=p_scope_type AND scope_id=p_scope_id
       AND period_start=p_period_start AND status='active';

    IF b.hard_input_tokens IS NOT NULL
       AND used_input + p_reserved_input_tokens > b.hard_input_tokens
       THEN RAISE EXCEPTION 'hard input token budget exceeded'; END IF;
    IF b.hard_output_tokens IS NOT NULL
       AND used_output + p_reserved_output_tokens > b.hard_output_tokens
       THEN RAISE EXCEPTION 'hard output token budget exceeded'; END IF;

    SELECT warning_soft_tokens, warning_soft_cost
      INTO soft_token_warning, soft_cost_warning
      FROM platform_quota_reserve(
        p_reservation_id,p_tenant_id,p_run_id,p_scope_type,p_scope_id,
        p_period_start,p_reserved_tokens,p_reserved_cost_usd
      );
    UPDATE platform_quota_reservations
       SET reserved_input_tokens=p_reserved_input_tokens,
           reserved_output_tokens=p_reserved_output_tokens
     WHERE reservation_id=p_reservation_id AND status='active';
    RETURN QUERY SELECT soft_token_warning, soft_cost_warning;
END;
$quota$;
COMMIT;
