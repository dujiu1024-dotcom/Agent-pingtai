BEGIN;

CREATE TABLE IF NOT EXISTS platform_prompt_experiment_reports (
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id) ON DELETE CASCADE,
    experiment_id uuid NOT NULL,
    prompt_id text NOT NULL CHECK (length(prompt_id) BETWEEN 1 AND 256),
    dataset_id text NOT NULL CHECK (length(dataset_id) BETWEEN 1 AND 128),
    dataset_version integer NOT NULL CHECK (dataset_version > 0),
    baseline_variant text NOT NULL CHECK (length(baseline_variant) BETWEEN 1 AND 128),
    report jsonb NOT NULL CHECK (jsonb_typeof(report) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, experiment_id)
);

CREATE INDEX IF NOT EXISTS idx_prompt_experiment_reports_recent
    ON platform_prompt_experiment_reports(tenant_id, prompt_id, created_at DESC);

CREATE OR REPLACE FUNCTION platform_reject_prompt_experiment_report_mutation()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'prompt experiment reports are immutable'
      USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_prompt_experiment_reports_immutable
    ON platform_prompt_experiment_reports;
CREATE TRIGGER trg_prompt_experiment_reports_immutable
    BEFORE UPDATE OR DELETE ON platform_prompt_experiment_reports
    FOR EACH ROW EXECUTE FUNCTION platform_reject_prompt_experiment_report_mutation();

REVOKE UPDATE, DELETE, TRUNCATE ON platform_prompt_experiment_reports FROM PUBLIC;

COMMIT;
