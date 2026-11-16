-- Registry-native workbench task execution and durable actor-scoped task listing.
BEGIN;

-- Registry snapshots are the authoritative immutable Agent/Model references for new Runs.
-- Keep the legacy columns for compatibility with older readers, but remove legacy-table
-- foreign keys so registry-native definitions do not need shadow copies in legacy tables.
DO $$
DECLARE constraint_row record;
BEGIN
    FOR constraint_row IN
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'platform_runs'::regclass
          AND contype = 'f'
          AND confrelid IN (
              'platform_agents'::regclass,
              'platform_model_templates'::regclass
          )
    LOOP
        EXECUTE format(
            'ALTER TABLE platform_runs DROP CONSTRAINT %I',
            constraint_row.conname
        );
    END LOOP;
END $$;

ALTER TABLE platform_runs
    ADD COLUMN IF NOT EXISTS created_by text NOT NULL DEFAULT 'platform-system',
    ADD COLUMN IF NOT EXISTS idempotency_key text,
    ADD COLUMN IF NOT EXISTS request_digest char(64),
    ADD COLUMN IF NOT EXISTS agent_version integer,
    ADD COLUMN IF NOT EXISTS session_id text,
    ADD COLUMN IF NOT EXISTS dispatch_ready boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS preparation_owner text,
    ADD COLUMN IF NOT EXISTS preparation_lease_expires_at timestamptz;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'platform_runs'::regclass
          AND conname = 'ck_platform_runs_agent_version_positive'
    ) THEN
        ALTER TABLE platform_runs
            ADD CONSTRAINT ck_platform_runs_agent_version_positive
            CHECK (agent_version IS NULL OR agent_version >= 1);
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_platform_runs_actor_idempotency
    ON platform_runs (tenant_id, created_by, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_platform_runs_actor_recent
    ON platform_runs (tenant_id, created_by, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_platform_runs_preparation_lease
    ON platform_runs (preparation_lease_expires_at)
    WHERE task_id IS NOT NULL AND dispatch_ready = false;

CREATE INDEX IF NOT EXISTS idx_platform_runs_dispatch_ready
    ON platform_runs (status, created_at)
    WHERE task_id IS NOT NULL AND dispatch_ready = true;

CREATE INDEX IF NOT EXISTS idx_platform_runs_task_lookup
    ON platform_runs (tenant_id, task_id)
    WHERE task_id IS NOT NULL;

COMMENT ON COLUMN platform_runs.registry_snapshot IS
    'Authoritative immutable registry versions for registry-native Runs';
COMMENT ON COLUMN platform_runs.created_by IS
    'Authenticated actor who created the Run; used for workbench task visibility';
COMMENT ON COLUMN platform_runs.dispatch_ready IS
    'True only after the immutable Run registry snapshot has been persisted';
COMMENT ON COLUMN platform_runs.request_digest IS
    'Canonical digest of the idempotent task submission payload';

COMMIT;
