-- Persist task priority on the registry-native platform_runs queue.
BEGIN;

ALTER TABLE platform_runs
    ADD COLUMN IF NOT EXISTS priority integer NOT NULL DEFAULT 100;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'platform_runs'::regclass
          AND conname = 'ck_platform_runs_priority_range'
    ) THEN
        ALTER TABLE platform_runs
            ADD CONSTRAINT ck_platform_runs_priority_range
            CHECK (priority BETWEEN 0 AND 1000);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_platform_runs_dispatch_priority
    ON platform_runs (priority DESC, created_at ASC)
    WHERE task_id IS NOT NULL AND dispatch_ready = true
      AND status IN ('queued', 'interrupted', 'cancelling', 'running');

COMMIT;
