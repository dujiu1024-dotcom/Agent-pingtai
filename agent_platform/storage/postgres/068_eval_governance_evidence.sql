BEGIN;

CREATE TABLE IF NOT EXISTS platform_eval_comparisons (
    comparison_id uuid PRIMARY KEY,
    tenant_id text NOT NULL,
    baseline_run_id uuid NOT NULL REFERENCES platform_eval_runs(eval_run_id),
    candidate_run_id uuid NOT NULL REFERENCES platform_eval_runs(eval_run_id),
    comparison jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (baseline_run_id <> candidate_run_id)
);
CREATE INDEX IF NOT EXISTS idx_platform_eval_comparisons_tenant_recent
    ON platform_eval_comparisons(tenant_id,created_at DESC);

CREATE TABLE IF NOT EXISTS platform_eval_gate_evidence (
    evidence_id uuid PRIMARY KEY,
    tenant_id text NOT NULL,
    candidate_run_id uuid NOT NULL REFERENCES platform_eval_runs(eval_run_id),
    baseline_run_id uuid REFERENCES platform_eval_runs(eval_run_id),
    policy_id text NOT NULL,
    policy_version integer NOT NULL CHECK (policy_version > 0),
    policy_snapshot jsonb NOT NULL,
    passed boolean NOT NULL,
    failures text[] NOT NULL DEFAULT '{}',
    comparison_id uuid REFERENCES platform_eval_comparisons(comparison_id),
    evidence_refs text[] NOT NULL CHECK (cardinality(evidence_refs) > 0),
    evaluated_at_utc timestamptz NOT NULL,
    content_sha256 char(64) NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_platform_eval_gate_evidence_tenant_candidate
    ON platform_eval_gate_evidence(tenant_id,candidate_run_id,created_at DESC);

REVOKE UPDATE, DELETE, TRUNCATE
    ON platform_eval_comparisons,platform_eval_gate_evidence FROM PUBLIC;

COMMIT;
