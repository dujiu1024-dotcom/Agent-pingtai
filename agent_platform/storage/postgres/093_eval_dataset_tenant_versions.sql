-- Tenant-scoped immutable evaluation dataset versions and cases.
BEGIN;

CREATE TABLE IF NOT EXISTS platform_eval_dataset_versions (
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id) ON DELETE CASCADE,
    dataset_id text NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    created_by text NOT NULL,
    case_count integer NOT NULL CHECK (case_count BETWEEN 1 AND 2000),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, dataset_id, version),
    CHECK (length(dataset_id) BETWEEN 1 AND 128),
    CHECK (length(created_by) > 0)
);

CREATE TABLE IF NOT EXISTS platform_eval_dataset_cases (
    tenant_id text NOT NULL,
    dataset_id text NOT NULL,
    version integer NOT NULL,
    case_id text NOT NULL,
    input_text text NOT NULL,
    expected_output text NOT NULL,
    rubric jsonb NOT NULL,
    source_ref text,
    PRIMARY KEY (tenant_id, dataset_id, version, case_id),
    FOREIGN KEY (tenant_id, dataset_id, version)
      REFERENCES platform_eval_dataset_versions(tenant_id, dataset_id, version)
      ON DELETE CASCADE,
    CHECK (length(input_text) BETWEEN 1 AND 20000),
    CHECK (length(expected_output) BETWEEN 1 AND 20000),
    CHECK (jsonb_typeof(rubric) = 'object'),
    CHECK (length(case_id) BETWEEN 1 AND 128),
    CHECK (source_ref IS NULL OR length(source_ref) BETWEEN 1 AND 2048)
);

CREATE INDEX IF NOT EXISTS idx_eval_dataset_versions_recent
    ON platform_eval_dataset_versions(tenant_id, dataset_id, version DESC);
CREATE INDEX IF NOT EXISTS idx_eval_dataset_cases_lookup
    ON platform_eval_dataset_cases(tenant_id, dataset_id, version, case_id);

CREATE OR REPLACE FUNCTION platform_reject_eval_dataset_v2_mutation()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'evaluation dataset versions are immutable'
      USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_eval_dataset_versions_immutable ON platform_eval_dataset_versions;
CREATE TRIGGER trg_eval_dataset_versions_immutable
  BEFORE UPDATE OR DELETE ON platform_eval_dataset_versions
  FOR EACH ROW EXECUTE FUNCTION platform_reject_eval_dataset_v2_mutation();
DROP TRIGGER IF EXISTS trg_eval_dataset_cases_immutable ON platform_eval_dataset_cases;
CREATE TRIGGER trg_eval_dataset_cases_immutable
  BEFORE UPDATE OR DELETE ON platform_eval_dataset_cases
  FOR EACH ROW EXECUTE FUNCTION platform_reject_eval_dataset_v2_mutation();

REVOKE UPDATE, DELETE, TRUNCATE ON platform_eval_dataset_versions, platform_eval_dataset_cases FROM PUBLIC;

COMMIT;
