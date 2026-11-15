-- Add Run/Agent correlation to every tool audit event.
BEGIN;

ALTER TABLE platform_tool_audit
    ADD COLUMN IF NOT EXISTS run_id text,
    ADD COLUMN IF NOT EXISTS agent_id text;

CREATE INDEX IF NOT EXISTS idx_platform_tool_audit_run
    ON platform_tool_audit (tenant_id, run_id, created_at DESC)
    WHERE run_id IS NOT NULL;

COMMIT;
