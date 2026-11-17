BEGIN;
CREATE TABLE IF NOT EXISTS platform_prompt_rollout_policies (
 tenant_id text NOT NULL, prompt_id text NOT NULL,
 weights jsonb NOT NULL CHECK (jsonb_typeof(weights)='object'),
 tag_routes jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(tag_routes)='object'),
 tenant_routes jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(tenant_routes)='object'),
 updated_by text NOT NULL, revision bigint NOT NULL DEFAULT 1 CHECK (revision>0),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (tenant_id,prompt_id)
);
CREATE TABLE IF NOT EXISTS platform_prompt_rollout_decisions (
 decision_id bigserial PRIMARY KEY, tenant_id text NOT NULL, prompt_id text NOT NULL,
 variant_name text NOT NULL, matched_rule text NOT NULL,
 policy_revision bigint NOT NULL CHECK (policy_revision>0),
 subject_hash text NOT NULL CHECK (subject_hash ~ '^[a-f0-9]{64}$'),
 decided_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY (prompt_id,variant_name)
   REFERENCES platform_prompt_variants(prompt_id,variant_name)
);
CREATE INDEX IF NOT EXISTS idx_prompt_rollout_decisions_audit
 ON platform_prompt_rollout_decisions (tenant_id,prompt_id,decision_id DESC);
CREATE OR REPLACE FUNCTION platform_reject_prompt_rollout_decision_mutation()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 RAISE EXCEPTION 'prompt rollout decisions are append-only' USING ERRCODE='55000';
END;
$$;
DROP TRIGGER IF EXISTS trg_prompt_rollout_decisions_immutable ON platform_prompt_rollout_decisions;
CREATE TRIGGER trg_prompt_rollout_decisions_immutable
 BEFORE UPDATE OR DELETE ON platform_prompt_rollout_decisions
 FOR EACH ROW EXECUTE FUNCTION platform_reject_prompt_rollout_decision_mutation();
REVOKE UPDATE, DELETE, TRUNCATE ON platform_prompt_rollout_decisions FROM PUBLIC;
ALTER TABLE platform_prompt_audit_events
 DROP CONSTRAINT IF EXISTS platform_prompt_audit_events_action_check;
ALTER TABLE platform_prompt_audit_events
 ADD CONSTRAINT platform_prompt_audit_events_action_check CHECK (action IN (
 'version.registered','version.activated','version.rolled_back','canary.configured',
 'variant.created','variant.version_assigned','variant.deprecated',
 'rollout.policy_updated','rollout.route_decided'
 ));
COMMIT;
