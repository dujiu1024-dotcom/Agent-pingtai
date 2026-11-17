BEGIN;

CREATE TABLE IF NOT EXISTS platform_webhook_secret_revocations (
    tenant_id text NOT NULL,
    subscription_id uuid NOT NULL,
    secret_ref text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, subscription_id, secret_ref),
    FOREIGN KEY (tenant_id, subscription_id)
      REFERENCES platform_webhook_subscriptions(tenant_id, subscription_id)
      ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_webhook_secret_revocations_created
 ON platform_webhook_secret_revocations(created_at);

COMMIT;
