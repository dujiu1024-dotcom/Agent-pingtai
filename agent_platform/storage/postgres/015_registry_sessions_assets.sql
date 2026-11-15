-- Versioned control-plane registry and session/file metadata.
-- Binary objects remain in external object storage; this migration stores metadata only.
BEGIN;

CREATE TABLE IF NOT EXISTS platform_registry_versions (
    tenant_id       text NOT NULL REFERENCES platform_tenants(tenant_id),
    kind            text NOT NULL CHECK (kind IN ('agent','workflow','tool','skill','model_provider','model_template')),
    item_id         text NOT NULL,
    version         integer NOT NULL CHECK (version > 0),
    definition      jsonb NOT NULL,
    status          text NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','published','disabled','deprecated')),
    created_by      text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    published_at    timestamptz,
    PRIMARY KEY (tenant_id, kind, item_id, version)
);

CREATE TABLE IF NOT EXISTS platform_registry_active (
    tenant_id       text NOT NULL,
    kind            text NOT NULL,
    item_id         text NOT NULL,
    active_version  integer NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, kind, item_id),
    FOREIGN KEY (tenant_id, kind, item_id, active_version)
        REFERENCES platform_registry_versions(tenant_id, kind, item_id, version)
);

CREATE INDEX IF NOT EXISTS idx_registry_versions_recent
    ON platform_registry_versions (tenant_id, kind, item_id, version DESC);

CREATE TABLE IF NOT EXISTS platform_conversation_sessions (
    tenant_id       text NOT NULL REFERENCES platform_tenants(tenant_id),
    session_id      uuid NOT NULL,
    owner_id        text NOT NULL,
    project_id      text,
    title           text NOT NULL DEFAULT '',
    status          text NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','archived','deleted')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_platform_conversation_sessions_owner
    ON platform_conversation_sessions (tenant_id, owner_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS platform_messages (
    tenant_id       text NOT NULL,
    message_id      uuid NOT NULL,
    session_id      uuid NOT NULL,
    role            text NOT NULL CHECK (role IN ('user','assistant','system','tool','agent')),
    content         text NOT NULL,
    actor_id        text NOT NULL,
    task_id         text,
    run_id          text,
    asset_ids       jsonb NOT NULL DEFAULT '[]'::jsonb,
    metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, message_id),
    FOREIGN KEY (tenant_id, session_id)
        REFERENCES platform_conversation_sessions(tenant_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_platform_messages_session
    ON platform_messages (tenant_id, session_id, created_at, message_id);
CREATE INDEX IF NOT EXISTS idx_platform_messages_run
    ON platform_messages (tenant_id, run_id)
    WHERE run_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS platform_file_assets (
    tenant_id       text NOT NULL REFERENCES platform_tenants(tenant_id),
    asset_id        uuid NOT NULL,
    owner_id        text NOT NULL,
    project_id      text,
    session_id      uuid,
    object_key      text NOT NULL,
    filename        text NOT NULL,
    media_type      text NOT NULL,
    size_bytes      bigint NOT NULL CHECK (size_bytes > 0),
    sha256          char(64) NOT NULL,
    status          text NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','scanning','available','rejected','deleted')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, asset_id),
    UNIQUE (tenant_id, object_key),
    FOREIGN KEY (tenant_id, session_id)
        REFERENCES platform_conversation_sessions(tenant_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_platform_assets_owner
    ON platform_file_assets (tenant_id, owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_platform_assets_session
    ON platform_file_assets (tenant_id, session_id)
    WHERE session_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS platform_data_connections (
    tenant_id       text NOT NULL REFERENCES platform_tenants(tenant_id),
    connection_id   text NOT NULL,
    provider        text NOT NULL,
    display_name    text NOT NULL,
    secret_ref      text NOT NULL,
    scopes          jsonb NOT NULL DEFAULT '[]'::jsonb,
    status          text NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','revoked','error')),
    cursor          jsonb,
    last_synced_at  timestamptz,
    created_by      text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    revoked_at      timestamptz,
    PRIMARY KEY (tenant_id, connection_id)
);

CREATE INDEX IF NOT EXISTS idx_data_connections_active
    ON platform_data_connections (tenant_id, status, provider);

COMMIT;
