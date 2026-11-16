BEGIN;

CREATE TABLE IF NOT EXISTS platform_secret_versions (
    secret_ref text NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    status text NOT NULL CHECK (status IN ('pending','active','revoked')),
    created_at timestamptz NOT NULL,
    revoked_at timestamptz,
    PRIMARY KEY (secret_ref, version)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_platform_secret_one_active
    ON platform_secret_versions(secret_ref) WHERE status='active';

COMMIT;
