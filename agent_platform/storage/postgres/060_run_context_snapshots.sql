-- Upgrade Run context snapshots to the payload columns used by
-- PostgresRunContextSnapshotStore. Earlier migrations (048/058) created the
-- same table with context/reference_window; CREATE TABLE IF NOT EXISTS alone
-- cannot reconcile those schemas.
BEGIN;

CREATE TABLE IF NOT EXISTS platform_run_context_snapshots (
    tenant_id          text NOT NULL,
    run_id             uuid NOT NULL,
    revision           integer NOT NULL CHECK (revision > 0),
    snapshot_id        uuid NOT NULL,
    context_payload    jsonb NOT NULL,
    references_payload jsonb NOT NULL DEFAULT '[]'::jsonb,
    digest             text NOT NULL CHECK (length(digest) = 64),
    created_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, run_id, revision),
    FOREIGN KEY (tenant_id, run_id)
        REFERENCES platform_runs (tenant_id, run_id) ON DELETE CASCADE,
    CHECK (jsonb_typeof(context_payload) = 'object'),
    CHECK (jsonb_typeof(references_payload) = 'array')
);

ALTER TABLE platform_run_context_snapshots
    ADD COLUMN IF NOT EXISTS context_payload jsonb;
ALTER TABLE platform_run_context_snapshots
    ADD COLUMN IF NOT EXISTS references_payload jsonb;

-- Preserve rows created by either legacy migration generation.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'platform_run_context_snapshots'
          AND column_name = 'context'
    ) THEN
        EXECUTE 'UPDATE platform_run_context_snapshots
                 SET context_payload = context
                 WHERE context_payload IS NULL';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'platform_run_context_snapshots'
          AND column_name = 'reference_window'
    ) THEN
        EXECUTE 'UPDATE platform_run_context_snapshots
                 SET references_payload = reference_window
                 WHERE references_payload IS NULL';
    END IF;
END $$;

UPDATE platform_run_context_snapshots
SET references_payload = '[]'::jsonb
WHERE references_payload IS NULL;

ALTER TABLE platform_run_context_snapshots
    ALTER COLUMN context_payload SET NOT NULL;
ALTER TABLE platform_run_context_snapshots
    ALTER COLUMN references_payload SET DEFAULT '[]'::jsonb;
ALTER TABLE platform_run_context_snapshots
    ALTER COLUMN references_payload SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_run_context_snapshots_latest
    ON platform_run_context_snapshots (tenant_id, run_id, revision DESC);

COMMIT;
