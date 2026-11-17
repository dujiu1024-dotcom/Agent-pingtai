BEGIN;

CREATE TABLE IF NOT EXISTS platform_credentials (
    tenant_id text NOT NULL,
    credential_id text NOT NULL,
    provider text NOT NULL,
    display_name text NOT NULL,
    secret_ref text NOT NULL,
    scopes jsonb NOT NULL CHECK (jsonb_typeof(scopes)='array'),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
    revoked_at timestamptz,
    PRIMARY KEY (tenant_id, credential_id),
    UNIQUE (tenant_id, secret_ref),
    CHECK (credential_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
    CHECK (provider ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
    CHECK ((status='active' AND revoked_at IS NULL) OR status='revoked')
);

CREATE INDEX IF NOT EXISTS idx_platform_credentials_tenant_status
    ON platform_credentials(tenant_id,status,created_at DESC);

CREATE TABLE IF NOT EXISTS platform_credential_grants (
    tenant_id text NOT NULL,
    credential_id text NOT NULL,
    grantee_type text NOT NULL CHECK (grantee_type IN ('agent','workflow')),
    grantee_id text NOT NULL,
    scopes jsonb NOT NULL CHECK (jsonb_typeof(scopes)='array'),
    granted_by text NOT NULL,
    created_at timestamptz NOT NULL,
    revoked_at timestamptz,
    PRIMARY KEY (tenant_id,credential_id,grantee_type,grantee_id),
    FOREIGN KEY (tenant_id,credential_id)
      REFERENCES platform_credentials(tenant_id,credential_id) ON DELETE CASCADE,
    CHECK (grantee_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')
);

CREATE INDEX IF NOT EXISTS idx_platform_credential_grants_target
    ON platform_credential_grants(tenant_id,grantee_type,grantee_id)
    WHERE revoked_at IS NULL;

COMMIT;
