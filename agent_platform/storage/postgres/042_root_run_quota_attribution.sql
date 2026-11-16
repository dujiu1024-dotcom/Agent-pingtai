-- Root Run attribution for Run-scoped quota budgets and usage aggregation.
BEGIN;

ALTER TABLE platform_quota_budgets
    DROP CONSTRAINT IF EXISTS platform_quota_budgets_scope_type_check;
ALTER TABLE platform_quota_budgets
    ADD CONSTRAINT platform_quota_budgets_scope_type_check
    CHECK (scope_type IN ('tenant','project','agent','model','run','queue','worker_pool'));

ALTER TABLE platform_quota_reservations
    ADD COLUMN IF NOT EXISTS root_run_id uuid;
UPDATE platform_quota_reservations
   SET root_run_id = CASE WHEN scope_type = 'run' THEN scope_id::uuid ELSE run_id END
 WHERE root_run_id IS NULL;
ALTER TABLE platform_quota_reservations
    ALTER COLUMN root_run_id SET NOT NULL;
ALTER TABLE platform_quota_reservations
    ADD CONSTRAINT fk_quota_reservation_root_run
    FOREIGN KEY (tenant_id, root_run_id)
    REFERENCES platform_runs(tenant_id, run_id);

ALTER TABLE platform_usage_ledger
    ADD COLUMN IF NOT EXISTS root_run_id uuid;
DROP TRIGGER IF EXISTS trg_platform_usage_no_update_delete ON platform_usage_ledger;
UPDATE platform_usage_ledger u
   SET root_run_id = r.root_run_id
  FROM platform_quota_reservations r
 WHERE r.reservation_id = u.reservation_id
   AND u.root_run_id IS NULL;
ALTER TABLE platform_usage_ledger
    ALTER COLUMN root_run_id SET NOT NULL;
ALTER TABLE platform_usage_ledger
    ADD CONSTRAINT fk_usage_root_run
    FOREIGN KEY (tenant_id, root_run_id)
    REFERENCES platform_runs(tenant_id, run_id);
CREATE INDEX IF NOT EXISTS idx_platform_usage_root_run_time
    ON platform_usage_ledger(tenant_id, root_run_id, occurred_at);

CREATE TRIGGER trg_platform_usage_no_update_delete
    BEFORE UPDATE OR DELETE ON platform_usage_ledger
    FOR EACH ROW EXECUTE FUNCTION platform_reject_usage_mutation();

CREATE OR REPLACE FUNCTION platform_quota_set_root_run_id()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE reservation_root uuid;
BEGIN
    SELECT root_run_id INTO reservation_root
      FROM platform_quota_reservations
     WHERE reservation_id = NEW.reservation_id;
    IF reservation_root IS NULL THEN
        RAISE EXCEPTION 'usage reservation root Run attribution is missing';
    END IF;
    NEW.root_run_id := reservation_root;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_platform_usage_root_run_id ON platform_usage_ledger;
CREATE TRIGGER trg_platform_usage_root_run_id
    BEFORE INSERT ON platform_usage_ledger
    FOR EACH ROW EXECUTE FUNCTION platform_quota_set_root_run_id();

CREATE OR REPLACE FUNCTION platform_quota_reserve_split(
    p_reservation_id uuid, p_tenant_id text, p_run_id uuid,
    p_scope_type text, p_scope_id text, p_period_start timestamptz,
    p_reserved_tokens bigint, p_reserved_cost_usd numeric,
    p_reserved_input_tokens bigint, p_reserved_output_tokens bigint,
    p_root_run_id uuid
) RETURNS TABLE(warning_soft_tokens boolean, warning_soft_cost boolean)
LANGUAGE plpgsql AS $quota$
DECLARE
    soft_token_warning boolean;
    soft_cost_warning boolean;
BEGIN
    IF p_root_run_id IS NULL THEN
        RAISE EXCEPTION 'root_run_id is required';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM platform_runs
         WHERE tenant_id=p_tenant_id AND run_id=p_root_run_id
    ) THEN
        RAISE EXCEPTION 'root Run does not exist in tenant';
    END IF;
    IF p_scope_type = 'run' AND p_scope_id <> p_root_run_id::text THEN
        RAISE EXCEPTION 'Run budget scope must reference root_run_id';
    END IF;

    SELECT q.warning_soft_tokens, q.warning_soft_cost
      INTO soft_token_warning, soft_cost_warning
      FROM platform_quota_reserve_split(
        p_reservation_id,p_tenant_id,p_run_id,p_scope_type,p_scope_id,
        p_period_start,p_reserved_tokens,p_reserved_cost_usd,
        p_reserved_input_tokens,p_reserved_output_tokens
      ) AS q;
    UPDATE platform_quota_reservations
       SET root_run_id=p_root_run_id
     WHERE reservation_id=p_reservation_id AND status='active';
    RETURN QUERY SELECT soft_token_warning, soft_cost_warning;
END;
$quota$;

COMMIT;
