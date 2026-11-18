-- 死信处置与事件订阅。
-- 死信补 resolved_at/resolved_by，支持人工标记解决；订阅表承接旧系统 subscription/sync。
BEGIN;

ALTER TABLE platform_run_dead_letters
    ADD COLUMN IF NOT EXISTS resolved_at timestamptz,
    ADD COLUMN IF NOT EXISTS resolved_by text;

CREATE INDEX IF NOT EXISTS idx_platform_run_dead_letters_open
    ON platform_run_dead_letters (tenant_id, created_at DESC)
    WHERE resolved_at IS NULL;

CREATE TABLE IF NOT EXISTS platform_event_subscriptions (
    tenant_id       text NOT NULL REFERENCES platform_tenants(tenant_id) ON DELETE CASCADE,
    subscription_id uuid NOT NULL DEFAULT gen_random_uuid(),
    event_type      text NOT NULL
                    CHECK (event_type IN ('task.failed','run.failed','approval.pending','daily')),
    channel         text NOT NULL DEFAULT 'feishu' CHECK (channel IN ('feishu')),
    target          text NOT NULL DEFAULT '',
    enabled         boolean NOT NULL DEFAULT true,
    created_by      text NOT NULL DEFAULT '',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, subscription_id),
    UNIQUE (tenant_id, event_type, channel, target)
);

COMMIT;
