-- Monotonic fencing token for durable Run worker leases.
-- Each successful lease acquisition increments the token atomically. Workers
-- can include the observed token on lease-sensitive writes to reject stale owners.
BEGIN;

ALTER TABLE platform_runs
    ADD COLUMN IF NOT EXISTS lease_token bigint NOT NULL DEFAULT 0;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'platform_runs_lease_token_nonnegative'
          AND conrelid = 'platform_runs'::regclass
    ) THEN
        ALTER TABLE platform_runs
            ADD CONSTRAINT platform_runs_lease_token_nonnegative
            CHECK (lease_token >= 0);
    END IF;
END
$$;

COMMIT;
