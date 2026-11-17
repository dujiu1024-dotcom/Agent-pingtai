BEGIN;

CREATE TABLE IF NOT EXISTS platform_output_schemas (
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id) ON DELETE CASCADE,
    schema_id text NOT NULL CHECK (length(schema_id) BETWEEN 1 AND 128),
    schema_version integer NOT NULL CHECK (schema_version > 0),
    schema_json jsonb NOT NULL CHECK (jsonb_typeof(schema_json) = 'object'),
    schema_digest text NOT NULL CHECK (schema_digest ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, schema_id, schema_version)
);

CREATE INDEX IF NOT EXISTS idx_platform_output_schemas_created
    ON platform_output_schemas (tenant_id, created_at DESC);

CREATE OR REPLACE FUNCTION reject_platform_output_schema_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'published output schema versions are immutable';
END;
$$;

DROP TRIGGER IF EXISTS trg_platform_output_schemas_immutable
    ON platform_output_schemas;
CREATE TRIGGER trg_platform_output_schemas_immutable
    BEFORE UPDATE OR DELETE ON platform_output_schemas
    FOR EACH ROW EXECUTE FUNCTION reject_platform_output_schema_mutation();

COMMIT;
