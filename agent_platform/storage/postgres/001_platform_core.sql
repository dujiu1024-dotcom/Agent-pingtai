-- PostgreSQL 15+; pgvector extension is required for semantic retrieval.
-- All business records are tenant-scoped. Apply with a migration runner, not at import time.
BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS platform_tenants (
    tenant_id       text PRIMARY KEY,
    display_name    text NOT NULL,
    status          text NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'suspended', 'archived')),
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS platform_agents (
    tenant_id       text NOT NULL REFERENCES platform_tenants(tenant_id),
    agent_id        text NOT NULL,
    display_name    text NOT NULL,
    role_definition text NOT NULL,
    skill_refs      jsonb NOT NULL DEFAULT '[]'::jsonb,
    status          text NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'disabled', 'draft')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, agent_id)
);

CREATE TABLE IF NOT EXISTS platform_model_templates (
    tenant_id       text NOT NULL REFERENCES platform_tenants(tenant_id),
    template_id     text NOT NULL,
    version         integer NOT NULL CHECK (version > 0),
    definition      jsonb NOT NULL,
    status          text NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'active', 'deprecated')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL,
    PRIMARY KEY (tenant_id, template_id, version)
);

CREATE TABLE IF NOT EXISTS platform_runs (
    tenant_id       text NOT NULL REFERENCES platform_tenants(tenant_id),
    run_id          uuid NOT NULL,
    project_id      text,
    task_id         text,
    agent_id        text NOT NULL,
    template_id     text NOT NULL,
    template_version integer NOT NULL,
    workflow_id     text,
    status          text NOT NULL
                    CHECK (status IN ('queued', 'running', 'paused', 'succeeded', 'failed', 'cancelled')),
    input           jsonb NOT NULL DEFAULT '{}'::jsonb,
    output          jsonb,
    usage           jsonb NOT NULL DEFAULT '{}'::jsonb,
    trace_id        text,
    started_at      timestamptz,
    finished_at     timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, run_id),
    FOREIGN KEY (tenant_id, agent_id)
        REFERENCES platform_agents(tenant_id, agent_id),
    FOREIGN KEY (tenant_id, template_id, template_version)
        REFERENCES platform_model_templates(tenant_id, template_id, version)
);

CREATE TABLE IF NOT EXISTS blackboard_entries (
    tenant_id       text NOT NULL REFERENCES platform_tenants(tenant_id),
    entry_id        uuid NOT NULL,
    scope           text NOT NULL CHECK (scope IN ('run', 'task', 'project', 'tenant')),
    scope_id        text NOT NULL,
    kind            text NOT NULL CHECK (
        kind IN ('fact', 'claim', 'evidence', 'challenge', 'conflict', 'decision', 'artifact', 'note')
    ),
    version         integer NOT NULL CHECK (version > 0),
    status          text NOT NULL CHECK (
        status IN ('proposed', 'accepted', 'challenged', 'superseded', 'rejected')
    ),
    content         jsonb NOT NULL,
    source_refs     jsonb NOT NULL DEFAULT '[]'::jsonb,
    author_id       text NOT NULL,
    correlation_id  uuid NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, entry_id, version)
);

CREATE INDEX IF NOT EXISTS idx_blackboard_scope
    ON blackboard_entries (tenant_id, scope, scope_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_blackboard_kind
    ON blackboard_entries (tenant_id, kind, status, created_at DESC);

CREATE TABLE IF NOT EXISTS blackboard_events (
    tenant_id       text NOT NULL REFERENCES platform_tenants(tenant_id),
    event_id        uuid NOT NULL,
    event_type      text NOT NULL,
    scope           text NOT NULL,
    scope_id        text NOT NULL,
    entry_id        uuid NOT NULL,
    entry_version   integer NOT NULL,
    actor_id        text NOT NULL,
    payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
    correlation_id  uuid NOT NULL,
    occurred_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, event_id),
    FOREIGN KEY (tenant_id, entry_id, entry_version)
        REFERENCES blackboard_entries(tenant_id, entry_id, version)
);

CREATE TABLE IF NOT EXISTS platform_approvals (
    tenant_id       text NOT NULL REFERENCES platform_tenants(tenant_id),
    approval_id     uuid NOT NULL,
    run_id          uuid,
    action          text NOT NULL,
    resource        text NOT NULL,
    risk_level      smallint NOT NULL CHECK (risk_level BETWEEN 2 AND 3),
    action_hash     text NOT NULL,
    requested_by    text NOT NULL,
    decided_by      text,
    status          text NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected', 'expired', 'consumed')),
    expires_at      timestamptz NOT NULL,
    decided_at      timestamptz,
    consumed_at     timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, approval_id),
    FOREIGN KEY (tenant_id, run_id) REFERENCES platform_runs(tenant_id, run_id)
);

CREATE TABLE IF NOT EXISTS platform_outbox (
    tenant_id       text NOT NULL REFERENCES platform_tenants(tenant_id),
    outbox_id       bigserial,
    event_id        uuid NOT NULL,
    topic           text NOT NULL,
    payload         jsonb NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    published_at    timestamptz,
    attempts        integer NOT NULL DEFAULT 0,
    last_error      text,
    lease_token     uuid,
    lease_until     timestamptz,
    PRIMARY KEY (tenant_id, outbox_id),
    UNIQUE (tenant_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_platform_outbox_pending
    ON platform_outbox (created_at)
    WHERE published_at IS NULL;

CREATE TABLE IF NOT EXISTS platform_tool_audit (
    tenant_id       text NOT NULL REFERENCES platform_tenants(tenant_id),
    audit_id        bigserial,
    actor_id        text NOT NULL,
    action          text NOT NULL,
    resource        text NOT NULL,
    action_digest   text NOT NULL,
    outcome         text NOT NULL CHECK (
        outcome IN ('denied', 'started', 'succeeded', 'failed')
    ),
    detail          text NOT NULL DEFAULT '',
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, audit_id)
);

CREATE INDEX IF NOT EXISTS idx_platform_tool_audit_recent
    ON platform_tool_audit (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS platform_embeddings (
    tenant_id       text NOT NULL REFERENCES platform_tenants(tenant_id),
    object_type     text NOT NULL,
    object_id       text NOT NULL,
    chunk_id        text NOT NULL,
    embedding_model text NOT NULL,
    embedding       vector,
    content_hash    text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, object_type, object_id, chunk_id, embedding_model)
);

COMMIT;
