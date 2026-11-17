BEGIN;

CREATE TABLE IF NOT EXISTS platform_quota_policies (
    policy_id text NOT NULL,
    tenant_id text,
    scope_type text NOT NULL CHECK (scope_type IN ('default','tenant','project','subject')),
    scope_id text NOT NULL,
    limits jsonb NOT NULL CHECK (jsonb_typeof(limits) = 'object' AND limits <> '{}'::jsonb),
    priority integer NOT NULL DEFAULT 100,
    enabled boolean NOT NULL DEFAULT true,
    version integer NOT NULL CHECK (version > 0),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scope_type, tenant_id, scope_id, policy_id),
    CHECK (
      (scope_type = 'default' AND tenant_id IS NULL AND scope_id = '*')
      OR (scope_type <> 'default' AND tenant_id IS NOT NULL AND length(tenant_id) > 0
          AND length(scope_id) > 0)
    )
);
CREATE INDEX IF NOT EXISTS idx_quota_policies_tenant_scope
    ON platform_quota_policies(tenant_id,scope_type,scope_id,enabled,priority DESC);

CREATE TABLE IF NOT EXISTS platform_quota_policy_audit (
    audit_id uuid PRIMARY KEY,
    tenant_id text,
    actor_id text NOT NULL,
    action text NOT NULL CHECK (action IN ('quota.policy.created','quota.policy.updated')),
    policy_id text NOT NULL,
    policy_version integer NOT NULL CHECK (policy_version > 0),
    before_state jsonb,
    after_state jsonb NOT NULL,
    occurred_at timestamptz NOT NULL,
    payload_sha256 char(64) NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_quota_policy_audit_tenant_time
    ON platform_quota_policy_audit(tenant_id,occurred_at DESC);
REVOKE UPDATE, DELETE, TRUNCATE ON platform_quota_policy_audit FROM PUBLIC;

COMMIT;
