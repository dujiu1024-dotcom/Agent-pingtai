BEGIN;

CREATE TABLE IF NOT EXISTS platform_subject_export_operations (
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id),
    operation_id text NOT NULL,
    subject_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('planned','running','partial','completed','failed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, operation_id)
);

CREATE TABLE IF NOT EXISTS platform_subject_export_sources (
    tenant_id text NOT NULL,
    operation_id text NOT NULL,
    source_name text NOT NULL,
    status text NOT NULL CHECK (status IN ('running','completed','failed')),
    record_count bigint NOT NULL DEFAULT 0 CHECK (record_count >= 0),
    sha256 text,
    error_code text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, operation_id, source_name),
    FOREIGN KEY (tenant_id, operation_id)
      REFERENCES platform_subject_export_operations(tenant_id, operation_id)
      ON DELETE CASCADE,
    CHECK (
      (status = 'completed' AND sha256 IS NOT NULL AND error_code IS NULL)
      OR (status = 'failed' AND error_code IS NOT NULL)
      OR status = 'running'
    )
);

CREATE INDEX IF NOT EXISTS idx_subject_export_operations_status
 ON platform_subject_export_operations(tenant_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_subject_export_sources_status
 ON platform_subject_export_sources(tenant_id, operation_id, status);

COMMIT;
