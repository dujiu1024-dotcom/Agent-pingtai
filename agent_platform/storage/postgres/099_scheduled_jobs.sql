-- 平台级定时作业：作业定义、执行历史与通知去重。
-- 旧系统 agency 的 tick/due_scan/daily_brief 等作业统一收敛到这里。
BEGIN;

CREATE TABLE IF NOT EXISTS platform_scheduled_jobs (
    tenant_id        text NOT NULL REFERENCES platform_tenants(tenant_id) ON DELETE CASCADE,
    job_id           text NOT NULL,
    name             text NOT NULL,
    description      text NOT NULL DEFAULT '',
    interval_seconds integer NOT NULL CHECK (interval_seconds BETWEEN 10 AND 2592000),
    enabled          boolean NOT NULL DEFAULT true,
    status           text NOT NULL DEFAULT 'idle' CHECK (status IN ('idle','running')),
    last_run_at      timestamptz,
    last_result      text CHECK (last_result IS NULL OR last_result IN ('succeeded','failed','skipped')),
    last_message     text,
    last_duration_ms integer,
    next_run_at      timestamptz NOT NULL DEFAULT now(),
    run_count        bigint NOT NULL DEFAULT 0,
    failure_count    bigint NOT NULL DEFAULT 0,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, job_id)
);

-- 调度循环只扫描启用且到期的作业，用部分索引避免全表扫描。
CREATE INDEX IF NOT EXISTS idx_platform_scheduled_jobs_due
    ON platform_scheduled_jobs (next_run_at)
    WHERE enabled;

CREATE TABLE IF NOT EXISTS platform_scheduled_job_runs (
    tenant_id   text NOT NULL,
    run_id      uuid NOT NULL DEFAULT gen_random_uuid(),
    job_id      text NOT NULL,
    trigger     text NOT NULL DEFAULT 'schedule'
                CHECK (trigger IN ('schedule','manual','startup')),
    status      text NOT NULL CHECK (status IN ('succeeded','failed','skipped')),
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    duration_ms integer,
    message     text,
    detail      jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (tenant_id, run_id),
    FOREIGN KEY (tenant_id, job_id)
        REFERENCES platform_scheduled_jobs(tenant_id, job_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_platform_scheduled_job_runs_recent
    ON platform_scheduled_job_runs (tenant_id, started_at DESC);

-- 通知去重台账：同一去重键只推送一次，避免巡检类作业反复轰炸。
CREATE TABLE IF NOT EXISTS platform_notification_log (
    tenant_id  text NOT NULL,
    channel    text NOT NULL,
    dedupe_key text NOT NULL,
    status     text NOT NULL CHECK (status IN ('sent','failed','skipped')),
    message    text,
    detail     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, channel, dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_platform_notification_log_recent
    ON platform_notification_log (tenant_id, created_at DESC);

COMMIT;
