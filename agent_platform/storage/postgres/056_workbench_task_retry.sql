-- Preserve retry lineage when a failed/timed-out task is retried as a new Run.
BEGIN;

ALTER TABLE platform_runs
    ADD COLUMN IF NOT EXISTS retry_of_run_id uuid;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'platform_runs'::regclass
          AND conname = 'fk_platform_runs_retry_of'
    ) THEN
        ALTER TABLE platform_runs
            ADD CONSTRAINT fk_platform_runs_retry_of
            FOREIGN KEY (tenant_id, retry_of_run_id)
            REFERENCES platform_runs (tenant_id, run_id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_platform_runs_retry_of
    ON platform_runs (tenant_id, retry_of_run_id)
    WHERE retry_of_run_id IS NOT NULL;

COMMIT;
