-- Durable extraction staging. Page rows and cursor checkpoints are committed atomically.
CREATE TABLE IF NOT EXISTS platform_data_migration_staging (
    tenant_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    source_key TEXT NOT NULL,
    source_payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, batch_id, source_key)
);

CREATE TABLE IF NOT EXISTS platform_data_migration_checkpoints (
    tenant_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    next_cursor TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, batch_id)
);

CREATE INDEX IF NOT EXISTS idx_data_migration_staging_batch
    ON platform_data_migration_staging (tenant_id, batch_id, source_key);
