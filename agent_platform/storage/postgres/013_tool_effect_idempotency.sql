-- Durable idempotency ledger for side-effecting tool calls.
BEGIN;
CREATE TABLE IF NOT EXISTS platform_tool_effects (
    tenant_id       text NOT NULL,
    run_id          uuid NOT NULL,
    call_id         text NOT NULL,
    action_digest   char(64) NOT NULL,
    actor_id        text NOT NULL,
    state           text NOT NULL CHECK (state IN ('pending','succeeded','failed')),
    result          jsonb,
    error           text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, run_id, call_id),
    FOREIGN KEY (tenant_id, run_id)
        REFERENCES platform_runs(tenant_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_tool_effects_pending
    ON platform_tool_effects (updated_at)
    WHERE state = 'pending';
CREATE INDEX IF NOT EXISTS idx_tool_effects_failed
    ON platform_tool_effects (tenant_id, updated_at)
    WHERE state = 'failed';
COMMIT;
