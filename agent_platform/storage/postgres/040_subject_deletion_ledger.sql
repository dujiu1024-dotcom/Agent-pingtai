BEGIN;

CREATE TABLE IF NOT EXISTS platform_subject_deletion_operations (
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id),
    operation_id text NOT NULL,
    subject_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('planned','running','partial','completed','failed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, operation_id)
);

CREATE TABLE IF NOT EXISTS platform_subject_deletion_sources (
    tenant_id text NOT NULL,
    operation_id text NOT NULL,
    source_name text NOT NULL,
    status text NOT NULL CHECK (status IN ('running','completed','failed')),
    error_code text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, operation_id, source_name),
    FOREIGN KEY (tenant_id, operation_id)
      REFERENCES platform_subject_deletion_operations(tenant_id, operation_id)
      ON DELETE CASCADE,
    CHECK (
      (status = 'failed' AND error_code IS NOT NULL)
      OR (status IN ('running','completed') AND error_code IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_subject_deletion_operations_status
 ON platform_subject_deletion_operations(tenant_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_subject_deletion_sources_status
 ON platform_subject_deletion_sources(tenant_id, operation_id, status);

COMMIT;
