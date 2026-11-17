BEGIN;

ALTER TABLE platform_webhook_secret_revocations
    ADD COLUMN IF NOT EXISTS lease_token uuid,
    ADD COLUMN IF NOT EXISTS lease_until timestamptz,
    ADD COLUMN IF NOT EXISTS lease_attempts integer NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_webhook_secret_revocations_claimable
    ON platform_webhook_secret_revocations(lease_until, created_at);

COMMIT;
