-- Durable Blackboard event delivery, independent of the source event log.
CREATE TABLE IF NOT EXISTS blackboard_event_deliveries (
    tenant_id text NOT NULL,
    event_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'pending'
      CHECK (status IN ('pending', 'processing', 'completed', 'manual')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    worker_id text,
    lease_token uuid,
    lease_expires_at timestamptz,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    last_error text,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, event_id),
    FOREIGN KEY (tenant_id, event_id)
      REFERENCES blackboard_events (tenant_id, event_id) ON DELETE CASCADE,
    CHECK (
      (status = 'processing' AND worker_id IS NOT NULL
       AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
      OR
      (status <> 'processing' AND worker_id IS NULL
       AND lease_token IS NULL AND lease_expires_at IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS ix_blackboard_delivery_claim
  ON blackboard_event_deliveries (tenant_id, status, next_attempt_at, event_id);
CREATE INDEX IF NOT EXISTS ix_blackboard_delivery_expired_lease
  ON blackboard_event_deliveries (tenant_id, lease_expires_at)
  WHERE status = 'processing';

CREATE TABLE IF NOT EXISTS blackboard_event_delivery_interventions (
    intervention_id bigserial PRIMARY KEY,
    tenant_id text NOT NULL,
    event_id uuid NOT NULL,
    actor_id text NOT NULL,
    reason text NOT NULL,
    action text NOT NULL CHECK (action IN ('redrive')),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, event_id)
      REFERENCES blackboard_event_deliveries (tenant_id, event_id) ON DELETE CASCADE
);
