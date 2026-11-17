-- Correlate multiple scope reservations that protect one execution.
-- The reservation rows remain the accounting authority; the group id is
-- correlation metadata and does not duplicate usage ledger events.
BEGIN;

ALTER TABLE platform_quota_reservations
    ADD COLUMN IF NOT EXISTS reservation_group_id uuid;

CREATE INDEX IF NOT EXISTS idx_platform_quota_reservations_group
    ON platform_quota_reservations(tenant_id, reservation_group_id)
    WHERE reservation_group_id IS NOT NULL;

COMMIT;
