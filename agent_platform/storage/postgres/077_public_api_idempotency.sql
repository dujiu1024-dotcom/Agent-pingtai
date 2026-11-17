BEGIN;
CREATE TABLE IF NOT EXISTS platform_api_idempotency (
 tenant_id text NOT NULL, principal_id text NOT NULL, http_method text NOT NULL, route text NOT NULL,
 idempotency_key varchar(128) NOT NULL, request_hash char(64) NOT NULL,
 state text NOT NULL CHECK (state IN ('pending','completed')),
 owner_token uuid, lease_until timestamptz, response_status integer, response_headers jsonb,
 response_body bytea, created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
 expires_at timestamptz NOT NULL,
 PRIMARY KEY (tenant_id,principal_id,http_method,route,idempotency_key),
 CHECK ((state='pending' AND owner_token IS NOT NULL AND response_status IS NULL AND response_body IS NULL) OR (state='completed' AND owner_token IS NULL AND lease_until IS NULL AND response_status BETWEEN 200 AND 599 AND response_body IS NOT NULL AND completed_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_platform_api_idempotency_expiry ON platform_api_idempotency(expires_at);
COMMIT;