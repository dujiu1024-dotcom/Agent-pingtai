-- Durable file processing queue with tenant-scoped leases and retry state.
BEGIN;

CREATE TABLE IF NOT EXISTS platform_file_processing_jobs (
    tenant_id text NOT NULL,
    job_id uuid NOT NULL DEFAULT gen_random_uuid(),
    asset_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','running','succeeded','failed','dead')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts integer NOT NULL DEFAULT 5 CHECK (max_attempts BETWEEN 1 AND 20),
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_owner text,
    lease_expires_at timestamptz,
    last_error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, job_id),
    UNIQUE (tenant_id, asset_id),
    FOREIGN KEY (tenant_id, asset_id)
        REFERENCES platform_file_assets(tenant_id, asset_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_platform_file_processing_claim
    ON platform_file_processing_jobs (status, available_at, lease_expires_at, created_at);

COMMIT;
