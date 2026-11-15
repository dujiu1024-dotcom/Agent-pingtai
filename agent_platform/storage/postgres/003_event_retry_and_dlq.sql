BEGIN;

ALTER TABLE platform_event_receipts
    ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz,
    ADD COLUMN IF NOT EXISTS dead_lettered_at timestamptz;

ALTER TABLE platform_event_receipts
    DROP CONSTRAINT IF EXISTS platform_event_receipts_status_check;
ALTER TABLE platform_event_receipts
    ADD CONSTRAINT platform_event_receipts_status_check
    CHECK (status IN ('processing', 'completed', 'dead_lettered'));

CREATE INDEX IF NOT EXISTS idx_platform_event_receipts_retry
    ON platform_event_receipts (next_attempt_at)
    WHERE status = 'processing' AND next_attempt_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_platform_event_receipts_dead_letter
    ON platform_event_receipts (dead_lettered_at DESC)
    WHERE status = 'dead_lettered';

COMMIT;
