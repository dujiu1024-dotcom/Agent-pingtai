-- Durable connector cursor state; credentials remain in the external secret store.
BEGIN;
CREATE TABLE IF NOT EXISTS knowledge_connector_sync_state(
 tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id),
 connector_id text NOT NULL,
 cursor text,
 last_synced_at timestamptz,
 last_error text,
 updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(tenant_id,connector_id)
);
COMMIT;
