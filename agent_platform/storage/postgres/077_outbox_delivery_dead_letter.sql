BEGIN;

ALTER TABLE platform_outbox
  ADD COLUMN IF NOT EXISTS max_attempts integer NOT NULL DEFAULT 12
    CHECK (max_attempts BETWEEN 1 AND 100),
  ADD COLUMN IF NOT EXISTS dead_lettered_at timestamptz;

-- Stop retrying rows that exhausted their configured delivery budget.
UPDATE platform_outbox
SET dead_lettered_at = COALESCE(dead_lettered_at, now()),
    next_attempt_at = NULL,
    lease_token = NULL,
    lease_until = NULL
WHERE published_at IS NULL
  AND attempts >= max_attempts
  AND dead_lettered_at IS NULL
  AND (lease_until IS NULL OR lease_until < now());

CREATE INDEX IF NOT EXISTS idx_platform_outbox_dead_lettered
  ON platform_outbox(dead_lettered_at DESC)
  WHERE dead_lettered_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_platform_outbox_due_retry
  ON platform_outbox(next_attempt_at, created_at)
  WHERE published_at IS NULL AND dead_lettered_at IS NULL;

COMMIT;
