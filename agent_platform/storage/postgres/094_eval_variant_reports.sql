BEGIN;

CREATE TABLE IF NOT EXISTS platform_eval_variant_reports (
    report_id uuid PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id) ON DELETE CASCADE,
    dataset_id text NOT NULL,
    dataset_version integer NOT NULL CHECK (dataset_version > 0),
    variant_count integer NOT NULL CHECK (variant_count BETWEEN 2 AND 32),
    recommended_run_id uuid NOT NULL,
    report jsonb NOT NULL CHECK (jsonb_typeof(report)='object'),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (length(created_by) > 0),
    FOREIGN KEY (recommended_run_id) REFERENCES platform_eval_runs(eval_run_id)
);

CREATE OR REPLACE FUNCTION platform_reject_eval_variant_report_mutation()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'evaluation variant reports are immutable'
      USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_eval_variant_reports_immutable ON platform_eval_variant_reports;
CREATE TRIGGER trg_eval_variant_reports_immutable
  BEFORE UPDATE OR DELETE ON platform_eval_variant_reports
  FOR EACH ROW EXECUTE FUNCTION platform_reject_eval_variant_report_mutation();

REVOKE UPDATE, DELETE, TRUNCATE ON platform_eval_variant_reports FROM PUBLIC;

CREATE INDEX IF NOT EXISTS idx_eval_variant_reports_tenant_dataset_recent
    ON platform_eval_variant_reports(tenant_id,dataset_id,dataset_version,created_at DESC);

COMMIT;
