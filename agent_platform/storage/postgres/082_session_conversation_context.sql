-- Session conversation isolation, message token accounting and context snapshots.
-- Tenant identity is the mandatory outer isolation boundary.
BEGIN;

ALTER TABLE platform_conversation_sessions
    ADD COLUMN IF NOT EXISTS agent_id text,
    ADD COLUMN IF NOT EXISTS isolation_scope text NOT NULL DEFAULT 'user';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'platform_conversation_sessions_isolation_scope_check'
           AND conrelid = 'platform_conversation_sessions'::regclass
    ) THEN
        ALTER TABLE platform_conversation_sessions
            ADD CONSTRAINT platform_conversation_sessions_isolation_scope_check
            CHECK (
                isolation_scope IN ('user', 'agent', 'project', 'tenant')
                AND (isolation_scope <> 'agent' OR agent_id IS NOT NULL)
                AND (isolation_scope <> 'project' OR project_id IS NOT NULL)
            );
    END IF;
END
$$;

ALTER TABLE platform_messages
    ADD COLUMN IF NOT EXISTS token_usage jsonb;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'platform_messages_token_usage_check'
           AND conrelid = 'platform_messages'::regclass
    ) THEN
        ALTER TABLE platform_messages
            ADD CONSTRAINT platform_messages_token_usage_check
            CHECK (
                token_usage IS NULL
                OR CASE
                    WHEN jsonb_typeof(token_usage) = 'object'
                     AND COALESCE((token_usage->>'prompt_tokens') ~ '^[0-9]+$', false)
                     AND COALESCE((token_usage->>'completion_tokens') ~ '^[0-9]+$', false)
                     AND COALESCE((token_usage->>'total_tokens') ~ '^[0-9]+$', false)
                    THEN (token_usage->>'total_tokens')::numeric =
                         (token_usage->>'prompt_tokens')::numeric
                         + (token_usage->>'completion_tokens')::numeric
                    ELSE false
                   END
            );
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_platform_sessions_isolation
    ON platform_conversation_sessions
       (tenant_id, isolation_scope, owner_id, agent_id, project_id, updated_at DESC);

-- PostgreSQL requires a unique key matching the composite snapshot cursor FK.
CREATE UNIQUE INDEX IF NOT EXISTS idx_platform_messages_tenant_message_session
    ON platform_messages (tenant_id, message_id, session_id);

CREATE TABLE IF NOT EXISTS platform_context_window_snapshots (
    tenant_id       text NOT NULL,
    session_id      uuid NOT NULL,
    snapshot_id     uuid NOT NULL,
    version         integer NOT NULL CHECK (version > 0),
    run_id          text,
    last_message_id uuid,
    snapshot_data   jsonb NOT NULL CHECK (jsonb_typeof(snapshot_data) = 'object'),
    token_count     integer NOT NULL CHECK (token_count >= 0),
    max_tokens      integer NOT NULL CHECK (max_tokens > 0),
    created_by      text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, snapshot_id),
    UNIQUE (tenant_id, session_id, version),
    FOREIGN KEY (tenant_id, session_id)
        REFERENCES platform_conversation_sessions (tenant_id, session_id)
        ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, last_message_id, session_id)
        REFERENCES platform_messages (tenant_id, message_id, session_id),
    CHECK (token_count <= max_tokens)
);

CREATE INDEX IF NOT EXISTS idx_platform_context_snapshots_session
    ON platform_context_window_snapshots (tenant_id, session_id, version DESC);

CREATE INDEX IF NOT EXISTS idx_platform_context_snapshots_run
    ON platform_context_window_snapshots (tenant_id, run_id)
    WHERE run_id IS NOT NULL;

COMMIT;
