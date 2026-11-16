BEGIN;

CREATE TABLE IF NOT EXISTS platform_eval_datasets (
    dataset_id text NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    created_by text NOT NULL,
    case_count integer NOT NULL CHECK (case_count > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset_id,version)
);

CREATE TABLE IF NOT EXISTS platform_eval_cases (
    dataset_id text NOT NULL,
    dataset_version integer NOT NULL,
    case_id text NOT NULL,
    query text NOT NULL DEFAULT '',
    expected_terms text[] NOT NULL DEFAULT '{}',
    forbidden_terms text[] NOT NULL DEFAULT '{}',
    expected_document_ids text[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (dataset_id,dataset_version,case_id),
    FOREIGN KEY (dataset_id,dataset_version)
      REFERENCES platform_eval_datasets(dataset_id,version)
);

CREATE TABLE IF NOT EXISTS platform_eval_runs (
    eval_run_id uuid PRIMARY KEY,
    dataset_id text NOT NULL,
    dataset_version integer NOT NULL,
    model_key text NOT NULL,
    report jsonb NOT NULL,
    gate_passed boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (dataset_id,dataset_version)
      REFERENCES platform_eval_datasets(dataset_id,version)
);

CREATE INDEX IF NOT EXISTS idx_platform_eval_runs_recent
 ON platform_eval_runs(dataset_id,created_at DESC);

CREATE OR REPLACE FUNCTION platform_reject_eval_dataset_mutation()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'evaluation datasets are immutable'
      USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_eval_datasets_immutable ON platform_eval_datasets;
CREATE TRIGGER trg_eval_datasets_immutable
  BEFORE UPDATE OR DELETE ON platform_eval_datasets
  FOR EACH ROW EXECUTE FUNCTION platform_reject_eval_dataset_mutation();
DROP TRIGGER IF EXISTS trg_eval_cases_immutable ON platform_eval_cases;
CREATE TRIGGER trg_eval_cases_immutable
  BEFORE UPDATE OR DELETE ON platform_eval_cases
  FOR EACH ROW EXECUTE FUNCTION platform_reject_eval_dataset_mutation();

REVOKE UPDATE, DELETE, TRUNCATE ON platform_eval_datasets,platform_eval_cases FROM PUBLIC;

COMMIT;
