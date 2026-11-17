-- Per-request context-window ceiling for tenant/project/Agent/Run budgets.
BEGIN;
ALTER TABLE platform_quota_budgets
    ADD COLUMN IF NOT EXISTS hard_context_window_tokens bigint
        CHECK (hard_context_window_tokens IS NULL OR hard_context_window_tokens >= 0);
COMMIT;
