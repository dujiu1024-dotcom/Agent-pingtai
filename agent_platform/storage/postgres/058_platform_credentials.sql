-- C-27-01: credential metadata and explicit Agent/Workflow grants.
-- Secret values remain exclusively in the configured Vault/KMS SecretManager backend.
CREATE TABLE IF NOT EXISTS platform_credentials (
    credential_id uuid PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id) ON DELETE CASCADE,
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 256),
    credential_type text NOT NULL CHECK (credential_type IN ('oauth','api_key','service_account')),
    secret_ref text NOT NULL UNIQUE,
    scopes text[] NOT NULL CHECK (cardinality(scopes) > 0),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz,
    UNIQUE (tenant_id, credential_id)
);

CREATE INDEX IF NOT EXISTS idx_platform_credentials_tenant_active
    ON platform_credentials(tenant_id, credential_id) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS platform_credential_grants (
    tenant_id text NOT NULL,
    credential_id uuid NOT NULL,
    consumer_type text NOT NULL CHECK (consumer_type IN ('agent','workflow')),
    consumer_id text NOT NULL,
    scopes text[] NOT NULL CHECK (cardinality(scopes) > 0),
    granted_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, credential_id, consumer_type, consumer_id),
    FOREIGN KEY (tenant_id, credential_id)
        REFERENCES platform_credentials(tenant_id, credential_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_platform_credential_grants_consumer
    ON platform_credential_grants(tenant_id, consumer_type, consumer_id);
