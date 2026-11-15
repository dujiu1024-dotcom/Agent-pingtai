BEGIN;

CREATE TABLE IF NOT EXISTS platform_users (
    user_id uuid PRIMARY KEY,
    email text NOT NULL,
    email_normalized text NOT NULL UNIQUE,
    display_name text NOT NULL,
    password_hash text NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'disabled')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz
);

CREATE TABLE IF NOT EXISTS platform_tenant_memberships (
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id),
    user_id uuid NOT NULL REFERENCES platform_users(user_id),
    status text NOT NULL DEFAULT 'invited' CHECK (status IN ('invited', 'active', 'suspended', 'left')),
    joined_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_id)
);

CREATE TABLE IF NOT EXISTS platform_organizations (
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id),
    organization_id text NOT NULL,
    name text NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, organization_id)
);

CREATE TABLE IF NOT EXISTS platform_organization_memberships (
    tenant_id text NOT NULL,
    organization_id text NOT NULL,
    user_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'left')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, organization_id, user_id),
    FOREIGN KEY (tenant_id, organization_id) REFERENCES platform_organizations(tenant_id, organization_id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES platform_tenant_memberships(tenant_id, user_id)
);

CREATE TABLE IF NOT EXISTS platform_teams (
    tenant_id text NOT NULL,
    team_id text NOT NULL,
    organization_id text,
    name text NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, team_id),
    FOREIGN KEY (tenant_id, organization_id) REFERENCES platform_organizations(tenant_id, organization_id)
);

CREATE TABLE IF NOT EXISTS platform_team_memberships (
    tenant_id text NOT NULL,
    team_id text NOT NULL,
    user_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'left')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, team_id, user_id),
    FOREIGN KEY (tenant_id, team_id) REFERENCES platform_teams(tenant_id, team_id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES platform_tenant_memberships(tenant_id, user_id)
);

CREATE TABLE IF NOT EXISTS platform_projects (
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id),
    project_id text NOT NULL,
    organization_id text,
    slug text NOT NULL,
    name text NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived', 'suspended')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, project_id),
    UNIQUE (tenant_id, slug),
    FOREIGN KEY (tenant_id, organization_id) REFERENCES platform_organizations(tenant_id, organization_id)
);

CREATE TABLE IF NOT EXISTS platform_workspaces (
    tenant_id text NOT NULL,
    workspace_id text NOT NULL,
    project_id text NOT NULL,
    slug text NOT NULL,
    name text NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, workspace_id),
    UNIQUE (tenant_id, project_id, slug),
    FOREIGN KEY (tenant_id, project_id) REFERENCES platform_projects(tenant_id, project_id)
);

CREATE TABLE IF NOT EXISTS platform_project_members (
    tenant_id text NOT NULL,
    project_id text NOT NULL,
    user_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'left')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, project_id, user_id),
    FOREIGN KEY (tenant_id, project_id) REFERENCES platform_projects(tenant_id, project_id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES platform_tenant_memberships(tenant_id, user_id)
);

CREATE TABLE IF NOT EXISTS platform_permissions (
    permission_key text PRIMARY KEY,
    description text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS platform_roles (
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id),
    role_id text NOT NULL,
    name text NOT NULL,
    description text NOT NULL DEFAULT '',
    is_system boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, role_id),
    UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS platform_role_permissions (
    tenant_id text NOT NULL,
    role_id text NOT NULL,
    permission_key text NOT NULL REFERENCES platform_permissions(permission_key),
    PRIMARY KEY (tenant_id, role_id, permission_key),
    FOREIGN KEY (tenant_id, role_id) REFERENCES platform_roles(tenant_id, role_id)
);

CREATE TABLE IF NOT EXISTS platform_role_bindings (
    binding_id uuid PRIMARY KEY,
    tenant_id text NOT NULL,
    subject_type text NOT NULL CHECK (subject_type IN ('user', 'team', 'service_account', 'agent')),
    subject_id text NOT NULL,
    resource_type text NOT NULL,
    resource_id text NOT NULL,
    role_id text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz,
    FOREIGN KEY (tenant_id, role_id) REFERENCES platform_roles(tenant_id, role_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_platform_role_binding_active ON platform_role_bindings (tenant_id, subject_type, subject_id, resource_type, resource_id, role_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_platform_role_binding_subject ON platform_role_bindings (tenant_id, subject_type, subject_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_platform_role_binding_resource ON platform_role_bindings (tenant_id, resource_type, resource_id) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS platform_sessions (
    session_id text PRIMARY KEY,
    tenant_id text NOT NULL,
    user_id uuid NOT NULL,
    token_hash text NOT NULL UNIQUE,
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz,
    FOREIGN KEY (tenant_id, user_id) REFERENCES platform_tenant_memberships(tenant_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_platform_sessions_user ON platform_sessions (tenant_id, user_id, expires_at DESC);
CREATE INDEX IF NOT EXISTS idx_platform_sessions_active ON platform_sessions (expires_at) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS platform_api_credentials (
    credential_id uuid PRIMARY KEY,
    tenant_id text NOT NULL,
    user_id uuid NOT NULL,
    name text NOT NULL,
    token_hash text NOT NULL UNIQUE,
    scopes text[] NOT NULL DEFAULT ARRAY[]::text[],
    expires_at timestamptz,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    FOREIGN KEY (tenant_id, user_id) REFERENCES platform_tenant_memberships(tenant_id, user_id)
);

CREATE TABLE IF NOT EXISTS platform_service_accounts (
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id),
    service_account_id text NOT NULL,
    name text NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'disabled')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, service_account_id)
);

CREATE TABLE IF NOT EXISTS platform_agent_identities (
    tenant_id text NOT NULL,
    agent_id text NOT NULL,
    service_account_id text NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'disabled')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, agent_id),
    FOREIGN KEY (tenant_id, agent_id) REFERENCES platform_agents(tenant_id, agent_id),
    FOREIGN KEY (tenant_id, service_account_id) REFERENCES platform_service_accounts(tenant_id, service_account_id)
);

CREATE TABLE IF NOT EXISTS platform_invitations (
    invitation_id uuid PRIMARY KEY,
    tenant_id text NOT NULL,
    email_normalized text NOT NULL,
    role_id text NOT NULL,
    token_hash text NOT NULL UNIQUE,
    invited_by text NOT NULL,
    expires_at timestamptz NOT NULL,
    accepted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, role_id) REFERENCES platform_roles(tenant_id, role_id)
);
CREATE INDEX IF NOT EXISTS idx_platform_invitations_pending ON platform_invitations (tenant_id, expires_at) WHERE accepted_at IS NULL;

CREATE TABLE IF NOT EXISTS platform_identity_audit (
    audit_id bigserial PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id),
    actor_id text NOT NULL,
    action text NOT NULL,
    subject_id text NOT NULL,
    outcome text NOT NULL CHECK (outcome IN ('succeeded', 'denied', 'failed')),
    detail jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_platform_identity_audit_recent ON platform_identity_audit (tenant_id, created_at DESC);

COMMIT;
