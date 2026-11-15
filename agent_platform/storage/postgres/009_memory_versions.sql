-- Memory conflict states and immutable version history.
BEGIN;
ALTER TABLE platform_memories DROP CONSTRAINT IF EXISTS platform_memories_status_check;
ALTER TABLE platform_memories ADD CONSTRAINT platform_memories_status_check
 CHECK(status IN ('candidate','conflict','active','superseded','archived','expired','deleted'));
ALTER TABLE platform_memories ADD COLUMN IF NOT EXISTS conflict_key text;
CREATE INDEX IF NOT EXISTS idx_platform_memories_conflict
 ON platform_memories(tenant_id,scope,scope_id,conflict_key)
 WHERE conflict_key IS NOT NULL AND status IN ('active','conflict') AND deleted_at IS NULL;
CREATE TABLE IF NOT EXISTS platform_memory_versions(
 tenant_id text NOT NULL,
 memory_id uuid NOT NULL,
 version integer NOT NULL CHECK(version>0),
 scope text NOT NULL,
 scope_id text NOT NULL,
 content text NOT NULL,
 source_ref text NOT NULL,
 confidence real NOT NULL CHECK(confidence BETWEEN 0 AND 1),
 conflict_key text,
 status text NOT NULL,
 created_by text NOT NULL,
 confirmed_by text,
 expires_at timestamptz,
 metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
 recorded_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(tenant_id,memory_id,version),
 FOREIGN KEY(tenant_id,memory_id) REFERENCES platform_memories(tenant_id,memory_id) ON DELETE CASCADE
);
INSERT INTO platform_memory_versions(tenant_id,memory_id,version,scope,scope_id,content,source_ref,confidence,conflict_key,status,created_by,confirmed_by,expires_at,metadata,recorded_at)
 SELECT tenant_id,memory_id,version,scope,scope_id,content,source_ref,confidence,conflict_key,status,created_by,confirmed_by,expires_at,metadata,updated_at
 FROM platform_memories
 ON CONFLICT DO NOTHING;
COMMIT;
