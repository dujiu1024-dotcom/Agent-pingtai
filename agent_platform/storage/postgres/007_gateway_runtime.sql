-- Persistent Run-scoped Gateway credentials, endpoint ownership and checkpoints.
BEGIN;
CREATE TABLE IF NOT EXISTS platform_run_tokens (
 token_id uuid PRIMARY KEY, tenant_id text NOT NULL, run_id uuid NOT NULL,
 agent_id text NOT NULL, actor_id text NOT NULL, endpoint_url text NOT NULL,
 token_digest char(64) NOT NULL UNIQUE, scopes text[] NOT NULL DEFAULT ARRAY['platform:tools']::text[],
 issued_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL, revoked_at timestamptz,
 FOREIGN KEY (tenant_id,run_id) REFERENCES platform_runs(tenant_id,run_id));
CREATE INDEX IF NOT EXISTS idx_platform_run_tokens_active ON platform_run_tokens(tenant_id,run_id,expires_at) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_platform_run_tokens_expiry ON platform_run_tokens(expires_at);
CREATE TABLE IF NOT EXISTS platform_gateway_endpoints (
 tenant_id text NOT NULL, run_id uuid NOT NULL, instance_id text NOT NULL, endpoint_url text NOT NULL,
 status text NOT NULL CHECK(status IN ('active','inactive')), created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,run_id),
 FOREIGN KEY(tenant_id,run_id) REFERENCES platform_runs(tenant_id,run_id));
CREATE INDEX IF NOT EXISTS idx_platform_gateway_endpoint_instance ON platform_gateway_endpoints(instance_id,status);
CREATE TABLE IF NOT EXISTS platform_run_checkpoints (
 tenant_id text NOT NULL, run_id uuid NOT NULL, checkpoint_ref text NOT NULL,
 checkpoint_version integer NOT NULL CHECK(checkpoint_version>0),
 status text NOT NULL CHECK(status IN ('available','claimed','consumed','invalid')),
 lease_owner text, lease_until timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
 consumed_at timestamptz, PRIMARY KEY(tenant_id,run_id,checkpoint_version),
 FOREIGN KEY(tenant_id,run_id) REFERENCES platform_runs(tenant_id,run_id));
CREATE INDEX IF NOT EXISTS idx_platform_run_checkpoints_recovery
 ON platform_run_checkpoints(status,lease_until,created_at) WHERE status IN ('available','claimed');
COMMIT;
