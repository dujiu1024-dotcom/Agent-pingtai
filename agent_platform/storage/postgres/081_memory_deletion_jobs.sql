BEGIN;

CREATE TABLE IF NOT EXISTS platform_memory_deletion_jobs (
    job_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id text NOT NULL,
    memory_id text NOT NULL,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'leased', 'retry', 'completed', 'dead')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    lease_owner text,
    lease_token uuid,
    lease_expires_at timestamptz,
    last_error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, memory_id),
    CHECK (
        (status = 'leased' AND lease_owner IS NOT NULL
         AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR
        (status <> 'leased' AND lease_owner IS NULL
         AND lease_token IS NULL AND lease_expires_at IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_memory_deletion_jobs_claim
    ON platform_memory_deletion_jobs (next_attempt_at, created_at, job_id)
    WHERE status IN ('pending', 'retry');
CREATE INDEX IF NOT EXISTS idx_memory_deletion_jobs_expired_lease
    ON platform_memory_deletion_jobs (lease_expires_at, created_at, job_id)
    WHERE status = 'leased';
CREATE INDEX IF NOT EXISTS idx_memory_deletion_jobs_tenant_status
    ON platform_memory_deletion_jobs (tenant_id, status, updated_at DESC);

COMMIT;
