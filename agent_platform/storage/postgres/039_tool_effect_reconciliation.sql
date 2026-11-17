-- Explicit, audited reconciliation for ambiguous external tool side effects.
BEGIN;
ALTER TABLE platform_tool_effects
    DROP CONSTRAINT IF EXISTS platform_tool_effects_state_check;
ALTER TABLE platform_tool_effects
    ADD CONSTRAINT platform_tool_effects_state_check
    CHECK (state IN ('pending','succeeded','failed','retryable','compensated'));
ALTER TABLE platform_tool_effects
    ADD COLUMN IF NOT EXISTS resolution text,
    ADD COLUMN IF NOT EXISTS reconciled_by text,
    ADD COLUMN IF NOT EXISTS reconciliation_note text,
    ADD COLUMN IF NOT EXISTS reconciled_at timestamptz;

CREATE TABLE IF NOT EXISTS platform_tool_effect_reconciliations (
    reconciliation_id bigserial PRIMARY KEY,
    tenant_id text NOT NULL,
    run_id uuid NOT NULL,
    call_id text NOT NULL,
    operator_id text NOT NULL,
    resolution text NOT NULL CHECK (
        resolution IN ('confirmed_succeeded','confirmed_not_applied','compensated')
    ),
    note text NOT NULL CHECK (length(note) BETWEEN 1 AND 1000),
    evidence jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id,run_id,call_id)
        REFERENCES platform_tool_effects(tenant_id,run_id,call_id)
);
CREATE INDEX IF NOT EXISTS idx_tool_effect_reconciliations_run
    ON platform_tool_effect_reconciliations(tenant_id,run_id,created_at DESC);
REVOKE UPDATE, DELETE ON platform_tool_effect_reconciliations FROM PUBLIC;
COMMIT;
