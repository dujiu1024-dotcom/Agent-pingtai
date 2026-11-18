-- 持久化批次作业：数据库为唯一事实来源，后台执行只做进程内调度。
BEGIN;

CREATE TABLE IF NOT EXISTS platform_batch_jobs (
    batch_id uuid PRIMARY KEY,
    tenant_id text NOT NULL,
    actor_id text NOT NULL,
    template_id text NOT NULL,
    status text NOT NULL,
    total integer NOT NULL,
    succeeded integer NOT NULL DEFAULT 0,
    failed integer NOT NULL DEFAULT 0,
    max_concurrency integer NOT NULL,
    dataset jsonb NOT NULL,
    results jsonb NOT NULL DEFAULT '[]'::jsonb,
    error text,
    idempotency_key text,
    request_digest text,
    retry_keys jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    CONSTRAINT platform_batch_jobs_status_check
        CHECK (status IN ('queued','running','succeeded','failed','cancelled','interrupted')),
    CONSTRAINT platform_batch_jobs_total_check CHECK (total >= 1),
    CONSTRAINT platform_batch_jobs_concurrency_check
        CHECK (max_concurrency BETWEEN 1 AND 1000)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_batch_jobs_idempotency
    ON platform_batch_jobs (tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_batch_jobs_tenant_created
    ON platform_batch_jobs (tenant_id, created_at DESC);

COMMIT;
