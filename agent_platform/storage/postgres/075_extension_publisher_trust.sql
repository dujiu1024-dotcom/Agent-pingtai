BEGIN;

CREATE TABLE IF NOT EXISTS platform_trusted_publisher_keys (
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id) ON DELETE CASCADE,
    extension_id text NOT NULL,
    publisher_id text NOT NULL,
    key_id text NOT NULL,
    public_key_pem bytea NOT NULL CHECK (octet_length(public_key_pem) BETWEEN 1 AND 8192),
    not_before timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz,
    revoked_at timestamptz,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, extension_id, key_id),
    CHECK (expires_at IS NULL OR expires_at > not_before)
);

CREATE INDEX IF NOT EXISTS idx_trusted_publisher_keys_active
    ON platform_trusted_publisher_keys (tenant_id, extension_id, key_id)
    WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS platform_installed_extensions (
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id) ON DELETE CASCADE,
    extension_id text NOT NULL,
    manifest jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('disabled', 'enabled')),
    artifact_ref text NOT NULL CHECK (length(artifact_ref) BETWEEN 1 AND 512),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, extension_id)
);

CREATE INDEX IF NOT EXISTS idx_installed_extensions_status
    ON platform_installed_extensions (tenant_id, status, updated_at DESC);

COMMIT;
