-- Persist parse outcomes and per-attempt diagnostics for file processing.
BEGIN;

ALTER TABLE platform_file_processing_jobs
    ADD COLUMN IF NOT EXISTS result jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS last_error_message text,
    ADD COLUMN IF NOT EXISTS completed_at timestamptz,
    ADD COLUMN IF NOT EXISTS parser_name text;

CREATE TABLE IF NOT EXISTS platform_file_processing_attempts (
    tenant_id text NOT NULL,
    job_id uuid NOT NULL,
    attempt integer NOT NULL CHECK (attempt > 0),
    worker_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('running','succeeded','retrying','dead','abandoned')),
    parser_name text,
    result jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_code text,
    error_message text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    PRIMARY KEY (tenant_id, job_id, attempt),
    FOREIGN KEY (tenant_id, job_id)
        REFERENCES platform_file_processing_jobs(tenant_id, job_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_platform_file_processing_attempts_recent
    ON platform_file_processing_attempts (tenant_id, job_id, attempt DESC);
CREATE INDEX IF NOT EXISTS idx_platform_file_processing_dead
    ON platform_file_processing_jobs (tenant_id, updated_at DESC)
    WHERE status = 'dead';

COMMIT;
