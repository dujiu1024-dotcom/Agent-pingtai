BEGIN;

CREATE TABLE IF NOT EXISTS platform_connector_cleanup_jobs (
    tenant_id text NOT NULL,
    job_id uuid NOT NULL DEFAULT gen_random_uuid(),
    connection_id text NOT NULL,
    disposition text NOT NULL CHECK (disposition IN ('retain','delete')),
    remote_revoked boolean NOT NULL DEFAULT false,
    sink_cleaned boolean NOT NULL DEFAULT false,
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
    PRIMARY KEY (tenant_id,job_id),
    UNIQUE (tenant_id,connection_id,disposition),
    FOREIGN KEY (tenant_id,connection_id)
        REFERENCES platform_data_connections(tenant_id,connection_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_platform_connector_cleanup_claim
    ON platform_connector_cleanup_jobs (status,available_at,lease_expires_at,created_at);

COMMIT;
