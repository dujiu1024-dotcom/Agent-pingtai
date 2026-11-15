BEGIN;

CREATE TABLE IF NOT EXISTS platform_webhook_subscriptions (
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id),
    subscription_id uuid NOT NULL,
    target_url text NOT NULL,
    secret_ref text NOT NULL,
    topics text[] NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, subscription_id)
);

CREATE TABLE IF NOT EXISTS platform_webhook_deliveries (
    tenant_id text NOT NULL,
    delivery_id uuid NOT NULL,
    subscription_id uuid NOT NULL,
    event_id uuid NOT NULL,
    topic text NOT NULL,
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending'
      CHECK (status IN ('pending','leased','delivered','retrying','dead_lettered')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at timestamptz,
    lease_token uuid,
    lease_until timestamptz,
    response_code integer,
    last_error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz,
    PRIMARY KEY (tenant_id, delivery_id),
    UNIQUE (tenant_id, subscription_id, event_id),
    FOREIGN KEY (tenant_id, subscription_id)
      REFERENCES platform_webhook_subscriptions(tenant_id, subscription_id)
);

CREATE INDEX IF NOT EXISTS idx_webhook_delivery_due
 ON platform_webhook_deliveries(tenant_id,next_attempt_at,created_at)
 WHERE status IN ('pending','retrying','leased');

COMMIT;
