BEGIN;

ALTER TABLE platform_alert_events
  ADD COLUMN IF NOT EXISTS delivery_max_attempts integer NOT NULL DEFAULT 8
    CHECK (delivery_max_attempts BETWEEN 1 AND 100),
  ADD COLUMN IF NOT EXISTS next_delivery_at timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN IF NOT EXISTS on_call_user_id text,
  ADD COLUMN IF NOT EXISTS ack_due_at timestamptz;

ALTER TABLE platform_alert_events
  DROP CONSTRAINT IF EXISTS platform_alert_events_delivery_status_check;
ALTER TABLE platform_alert_events
  ADD CONSTRAINT platform_alert_events_delivery_status_check
    CHECK (delivery_status IN ('pending','sending','sent','failed','dead'));

UPDATE platform_alert_events
SET delivery_status='dead', delivery_claimed_at=NULL, next_delivery_at=NULL
WHERE delivery_status='sending'
  AND delivery_attempts >= delivery_max_attempts
  AND delivery_claimed_at < now() - interval '5 minutes';

CREATE INDEX IF NOT EXISTS idx_platform_alert_delivery_due
  ON platform_alert_events(next_delivery_at, fired_at)
  WHERE delivery_status IN ('pending','failed');

CREATE INDEX IF NOT EXISTS idx_platform_alert_ack_due
  ON platform_alert_events(scope_key, ack_due_at)
  WHERE status='open' AND ack_due_at IS NOT NULL;

COMMIT;
