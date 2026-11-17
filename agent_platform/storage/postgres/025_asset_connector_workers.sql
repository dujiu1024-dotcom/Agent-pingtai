BEGIN;

CREATE TABLE IF NOT EXISTS platform_asset_processing_jobs (
    tenant_id text NOT NULL,
    job_id uuid NOT NULL,
    asset_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','processing','completed','failed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts integer NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_owner text,
    lease_until timestamptz,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    PRIMARY KEY (tenant_id, job_id),
    UNIQUE (tenant_id, asset_id),
    FOREIGN KEY (tenant_id, asset_id)
        REFERENCES platform_file_assets(tenant_id, asset_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_platform_asset_processing_claim
    ON platform_asset_processing_jobs (status, available_at, created_at)
    WHERE status IN ('queued','processing');

CREATE TABLE IF NOT EXISTS platform_file_asset_contents (
    tenant_id text NOT NULL,
    asset_id uuid NOT NULL,
    extracted_text text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    parsed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, asset_id),
    FOREIGN KEY (tenant_id, asset_id)
        REFERENCES platform_file_assets(tenant_id, asset_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS platform_connector_sync_jobs (
    tenant_id text NOT NULL,
    job_id uuid NOT NULL,
    connection_id text NOT NULL,
    job_type text NOT NULL CHECK (job_type IN ('sync','revoke_cleanup')),
    disposition text NOT NULL DEFAULT 'retain' CHECK (disposition IN ('retain','delete')),
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','processing','completed','failed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts integer NOT NULL DEFAULT 8 CHECK (max_attempts > 0),
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_owner text,
    lease_until timestamptz,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    PRIMARY KEY (tenant_id, job_id),
    FOREIGN KEY (tenant_id, connection_id)
        REFERENCES platform_data_connections(tenant_id, connection_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_platform_connector_sync_claim
    ON platform_connector_sync_jobs (status, available_at, created_at)
    WHERE status IN ('queued','processing');

CREATE INDEX IF NOT EXISTS idx_platform_connector_sync_connection
    ON platform_connector_sync_jobs (tenant_id, connection_id, job_type, status);

CREATE TABLE IF NOT EXISTS platform_connector_documents (
    tenant_id text NOT NULL,
    connection_id text NOT NULL,
    source_id text NOT NULL,
    document jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz,
    deleted_at timestamptz,
    PRIMARY KEY (tenant_id, connection_id, source_id),
    FOREIGN KEY (tenant_id, connection_id)
        REFERENCES platform_data_connections(tenant_id, connection_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_platform_connector_documents_active
    ON platform_connector_documents (tenant_id, connection_id, updated_at DESC)
    WHERE revoked_at IS NULL AND deleted_at IS NULL;

COMMIT;
