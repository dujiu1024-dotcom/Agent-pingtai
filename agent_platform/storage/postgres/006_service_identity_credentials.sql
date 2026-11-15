BEGIN;

-- Machine identities use independent opaque credentials. Human API credentials
-- remain in platform_api_credentials and cannot authenticate an Agent.
CREATE TABLE IF NOT EXISTS platform_service_credentials (
    credential_id uuid PRIMARY KEY,
    tenant_id text NOT NULL,
    service_account_id text NOT NULL,
    agent_id text,
    name text NOT NULL,
    token_hash text NOT NULL UNIQUE,
    scopes text[] NOT NULL DEFAULT ARRAY[]::text[],
    expires_at timestamptz,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    FOREIGN KEY (tenant_id, service_account_id)
      REFERENCES platform_service_accounts(tenant_id, service_account_id),
    FOREIGN KEY (tenant_id, agent_id)
      REFERENCES platform_agent_identities(tenant_id, agent_id),
    CHECK (agent_id IS NULL OR agent_id <> '')
);
CREATE INDEX IF NOT EXISTS idx_platform_service_credentials_active
    ON platform_service_credentials (tenant_id, service_account_id, expires_at)
    WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_platform_service_credentials_agent
    ON platform_service_credentials (tenant_id, agent_id)
    WHERE revoked_at IS NULL;

COMMIT;
