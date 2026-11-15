BEGIN;

ALTER TABLE platform_approvals
    DROP CONSTRAINT IF EXISTS platform_approvals_status_check;

ALTER TABLE platform_approvals
    ADD CONSTRAINT platform_approvals_status_check
    CHECK (status IN ('pending','approved','rejected','expired','consumed','revoked'));

ALTER TABLE platform_approvals
    ADD COLUMN IF NOT EXISTS revoked_by text,
    ADD COLUMN IF NOT EXISTS revoked_at timestamptz;

CREATE INDEX IF NOT EXISTS idx_platform_approvals_expiry
    ON platform_approvals(tenant_id,expires_at)
    WHERE status='pending';

COMMIT;
