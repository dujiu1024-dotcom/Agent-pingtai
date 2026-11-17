BEGIN;

CREATE TABLE IF NOT EXISTS platform_eval_variant_runs (
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id) ON DELETE CASCADE,
    eval_run_id text NOT NULL,
    dataset_id text NOT NULL,
    dataset_version integer NOT NULL,
    prompt_sha256 text NOT NULL CHECK (prompt_sha256 ~ '^[0-9a-f]{64}$'),
    results jsonb NOT NULL CHECK (jsonb_typeof(results) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, eval_run_id),
    FOREIGN KEY (tenant_id, dataset_id, dataset_version)
      REFERENCES platform_eval_dataset_versions(tenant_id, dataset_id, version)
      ON DELETE RESTRICT,
    CHECK (length(eval_run_id) BETWEEN 1 AND 128)
);

CREATE INDEX IF NOT EXISTS idx_eval_variant_runs_recent
    ON platform_eval_variant_runs(tenant_id, dataset_id, created_at DESC);

COMMIT;
