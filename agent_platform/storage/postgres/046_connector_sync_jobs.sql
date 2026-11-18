-- Durable retryable connector synchronization requests.
BEGIN;

CREATE TABLE IF NOT EXISTS platform_connector_sync_jobs (
    tenant_id text NOT NULL,
    job_id uuid NOT NULL DEFAULT gen_random_uuid(),
    connection_id text NOT NULL,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','running','succeeded','dead')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts integer NOT NULL DEFAULT 8 CHECK (max_attempts BETWEEN 1 AND 20),
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_owner text,
    lease_expires_at timestamptz,
    last_error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    PRIMARY KEY (tenant_id,job_id),
    UNIQUE (tenant_id,connection_id),
    FOREIGN KEY (tenant_id,connection_id)
        REFERENCES platform_data_connections(tenant_id,connection_id) ON DELETE CASCADE
);

ALTER TABLE platform_connector_sync_jobs
    ADD COLUMN IF NOT EXISTS lease_expires_at timestamptz,
    ADD COLUMN IF NOT EXISTS last_error_code text;

CREATE INDEX IF NOT EXISTS idx_platform_connector_sync_claim
    ON platform_connector_sync_jobs (status,available_at,lease_expires_at,created_at);

COMMIT;
