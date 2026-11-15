BEGIN;

CREATE TABLE IF NOT EXISTS platform_data_migration_batches (
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id),
    batch_id text NOT NULL,
    source_system text NOT NULL,
    target_system text NOT NULL,
    object_type text NOT NULL,
    status text NOT NULL CHECK (status IN ('planned','running','validated','switched','rolled_back','failed')),
    source_count bigint NOT NULL DEFAULT 0 CHECK (source_count >= 0),
    target_count bigint NOT NULL DEFAULT 0 CHECK (target_count >= 0),
    source_checksum text,
    target_checksum text,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, batch_id)
);

CREATE TABLE IF NOT EXISTS platform_data_migration_rows (
    tenant_id text NOT NULL,
    batch_id text NOT NULL,
    source_key text NOT NULL,
    target_key text,
    payload_hash text NOT NULL,
    status text NOT NULL CHECK (status IN ('pending','imported','validated','skipped','failed')),
    error_code text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, batch_id, source_key),
    FOREIGN KEY (tenant_id, batch_id)
      REFERENCES platform_data_migration_batches(tenant_id, batch_id)
);

CREATE INDEX IF NOT EXISTS idx_data_migration_batch_status
 ON platform_data_migration_batches(tenant_id,status,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_data_migration_rows_status
 ON platform_data_migration_rows(tenant_id,batch_id,status);

COMMIT;
