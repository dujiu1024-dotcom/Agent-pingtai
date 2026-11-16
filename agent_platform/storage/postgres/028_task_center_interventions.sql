BEGIN;

CREATE TABLE IF NOT EXISTS platform_tasks (
    tenant_id text NOT NULL REFERENCES platform_tenants(tenant_id) ON DELETE CASCADE,
    task_id uuid NOT NULL DEFAULT gen_random_uuid(),
    actor_id text NOT NULL,
    agent_id text NOT NULL,
    project_id text,
    session_id uuid,
    input jsonb NOT NULL,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','running','paused','cancelling','succeeded','failed','cancelled')),
    run_id uuid,
    output jsonb,
    last_error text,
    idempotency_key text,
    lease_owner text,
    lease_expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    PRIMARY KEY (tenant_id, task_id),
    FOREIGN KEY (tenant_id, agent_id) REFERENCES platform_agents(tenant_id,agent_id),
    FOREIGN KEY (tenant_id, session_id) REFERENCES platform_conversation_sessions(tenant_id,session_id),
    FOREIGN KEY (tenant_id, run_id) REFERENCES platform_runs(tenant_id,run_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_platform_tasks_idempotency
    ON platform_tasks (tenant_id, actor_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_platform_tasks_queue
    ON platform_tasks (status, lease_expires_at, created_at);
CREATE INDEX IF NOT EXISTS idx_platform_tasks_actor
    ON platform_tasks (tenant_id, actor_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS platform_run_events (
    tenant_id text NOT NULL,
    event_id uuid NOT NULL DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL,
    task_id uuid,
    event_type text NOT NULL,
    actor_id text,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, event_id),
    FOREIGN KEY (tenant_id, run_id) REFERENCES platform_runs(tenant_id,run_id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, task_id) REFERENCES platform_tasks(tenant_id,task_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_platform_run_events_timeline
    ON platform_run_events (tenant_id, run_id, occurred_at, event_id);

CREATE TABLE IF NOT EXISTS platform_run_interventions (
    tenant_id text NOT NULL,
    intervention_id uuid NOT NULL DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL,
    actor_id text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('context','decision','takeover')),
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','applied','rejected')),
    created_at timestamptz NOT NULL DEFAULT now(),
    applied_at timestamptz,
    applied_by text,
    PRIMARY KEY (tenant_id, intervention_id),
    FOREIGN KEY (tenant_id, run_id) REFERENCES platform_runs(tenant_id,run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_platform_run_interventions_pending
    ON platform_run_interventions (tenant_id, run_id, created_at)
    WHERE status='pending';

COMMIT;
