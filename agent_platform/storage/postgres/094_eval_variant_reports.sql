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
    FOREIGN KEY (tenant_id,dataset_id,dataset_version)
      REFERENCES platform_eval_dataset_versions(tenant_id,dataset_id,version)
      ON DELETE CASCADE,
    CHECK (length(created_by) > 0)
);

CREATE INDEX IF NOT EXISTS idx_eval_variant_reports_tenant_dataset_recent
    ON platform_eval_variant_reports(tenant_id,dataset_id,dataset_version,created_at DESC);

COMMIT;
