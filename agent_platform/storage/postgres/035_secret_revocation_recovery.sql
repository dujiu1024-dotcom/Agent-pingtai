BEGIN;

ALTER TABLE platform_secret_versions
  ADD COLUMN IF NOT EXISTS backend_revoked_at timestamptz;

CREATE INDEX IF NOT EXISTS idx_platform_secret_revocation_pending
  ON platform_secret_versions(secret_ref,version)
  WHERE status='revoked' AND backend_revoked_at IS NULL;

COMMIT;
