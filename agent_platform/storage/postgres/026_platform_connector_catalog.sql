-- Tenant-scoped declarative connector manifests; runtime endpoints and credentials are host-owned.
CREATE TABLE IF NOT EXISTS platform_connector_manifests (
    tenant_id TEXT NOT NULL,
    connector_id TEXT NOT NULL,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    manifest JSONB NOT NULL,
    source TEXT NOT NULL DEFAULT 'local',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, connector_id),
    CHECK (length(tenant_id) BETWEEN 1 AND 256),
    CHECK (length(connector_id) BETWEEN 2 AND 128),
    CHECK (jsonb_typeof(manifest) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_platform_connector_manifests_tenant_name
    ON platform_connector_manifests (tenant_id, name);
