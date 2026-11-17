-- Durable bounded retries and dead-letter records for workbench Run workers.
BEGIN;

ALTER TABLE platform_runs
    ADD COLUMN IF NOT EXISTS worker_attempts integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS max_worker_attempts integer NOT NULL DEFAULT 5,
    ADD COLUMN IF NOT EXISTS dead_lettered_at timestamptz,
    ADD COLUMN IF NOT EXISTS dead_letter_reason text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'platform_runs'::regclass
          AND conname = 'ck_platform_runs_worker_attempt_bounds'
    ) THEN
        ALTER TABLE platform_runs
            ADD CONSTRAINT ck_platform_runs_worker_attempt_bounds
            CHECK (worker_attempts >= 0 AND max_worker_attempts BETWEEN 1 AND 100);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_platform_runs_workbench_retry
    ON platform_runs (status, created_at)
    WHERE task_id IS NOT NULL AND dispatch_ready = true
      AND dead_lettered_at IS NULL;

CREATE TABLE IF NOT EXISTS platform_run_dead_letters (
    tenant_id text NOT NULL,
    run_id uuid NOT NULL,
    attempt_count integer NOT NULL,
    max_attempts integer NOT NULL,
    reason text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, run_id),
    CHECK (attempt_count >= max_attempts AND max_attempts >= 1)
);

COMMENT ON TABLE platform_run_dead_letters IS
    'Terminal workbench worker failures retained for operator inspection and replay.';
COMMENT ON COLUMN platform_runs.worker_attempts IS
    'Number of durable worker claims; incremented atomically when a task is claimed.';
COMMENT ON COLUMN platform_runs.max_worker_attempts IS
    'Maximum worker claims before the task is terminally dead-lettered.';

COMMIT;
