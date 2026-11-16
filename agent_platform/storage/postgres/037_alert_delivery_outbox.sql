BEGIN;

ALTER TABLE platform_alert_events
  ADD COLUMN IF NOT EXISTS delivery_status text NOT NULL DEFAULT 'pending'
    CHECK (delivery_status IN ('pending','sending','sent','failed')),
  ADD COLUMN IF NOT EXISTS delivery_attempts integer NOT NULL DEFAULT 0
    CHECK (delivery_attempts >= 0),
  ADD COLUMN IF NOT EXISTS delivered_at timestamptz,
  ADD COLUMN IF NOT EXISTS delivery_claimed_at timestamptz,
  ADD COLUMN IF NOT EXISTS last_delivery_error text;

CREATE INDEX IF NOT EXISTS idx_platform_alert_delivery_pending
  ON platform_alert_events(delivery_status,fired_at)
  WHERE delivery_status IN ('pending','failed');

COMMIT;
