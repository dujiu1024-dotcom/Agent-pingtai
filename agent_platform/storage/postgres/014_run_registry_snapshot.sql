-- Pin immutable Agent/Model/Tool/Skill/Workflow registry versions to each Run.
BEGIN;
ALTER TABLE platform_runs
    ADD COLUMN IF NOT EXISTS registry_snapshot jsonb,
    ADD COLUMN IF NOT EXISTS registry_snapshot_digest char(64);
COMMIT;
