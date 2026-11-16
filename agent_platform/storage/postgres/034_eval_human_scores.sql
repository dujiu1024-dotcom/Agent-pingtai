BEGIN;

ALTER TABLE platform_eval_runs
  ADD COLUMN IF NOT EXISTS tenant_id text NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS platform_eval_human_scores (
    score_id uuid PRIMARY KEY,
    tenant_id text NOT NULL,
    eval_run_id uuid NOT NULL REFERENCES platform_eval_runs(eval_run_id),
    case_id text NOT NULL,
    reviewer_id text NOT NULL,
    score double precision NOT NULL CHECK (score >= 0 AND score <= 1),
    rationale text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (eval_run_id,case_id,reviewer_id)
);

CREATE INDEX IF NOT EXISTS idx_platform_eval_human_scores_tenant_run
  ON platform_eval_human_scores(tenant_id,eval_run_id,created_at);

COMMIT;
