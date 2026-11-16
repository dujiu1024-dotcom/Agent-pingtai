BEGIN;

CREATE TABLE IF NOT EXISTS platform_alert_events (
    event_id uuid PRIMARY KEY,
    scope_key text NOT NULL,
    dedupe_key text NOT NULL,
    rule_id text NOT NULL,
    metric_name text NOT NULL,
    observed_value double precision NOT NULL,
    threshold double precision NOT NULL,
    severity text NOT NULL CHECK (severity IN ('info','warning','critical')),
    fired_at timestamptz NOT NULL,
    description text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'open'
      CHECK (status IN ('open','acknowledged','resolved')),
    acknowledged_by text,
    acknowledged_at timestamptz,
    resolved_by text,
    resolved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_key,dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_platform_alert_events_open
  ON platform_alert_events(scope_key,status,fired_at DESC);

COMMIT;
