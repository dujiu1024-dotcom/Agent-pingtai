BEGIN;

CREATE TABLE IF NOT EXISTS graph_entities (
    tenant_id text NOT NULL,
    entity_id text NOT NULL,
    entity_type text NOT NULL,
    entity_name text NOT NULL,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    project_id text,
    source_domain text,
    source_type text,
    source_id text,
    source_version integer,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, entity_id),
    CHECK (length(btrim(tenant_id)) > 0),
    CHECK (length(btrim(entity_id)) BETWEEN 1 AND 200),
    CHECK (length(btrim(entity_type)) BETWEEN 1 AND 63),
    CHECK (length(btrim(entity_name)) BETWEEN 1 AND 500),
    CHECK (jsonb_typeof(attributes) = 'object'),
    CHECK (
        (source_domain IS NULL AND source_type IS NULL AND source_id IS NULL AND source_version IS NULL)
        OR
        (source_domain IN ('knowledge', 'memory', 'blackboard')
         AND source_type IS NOT NULL AND length(btrim(source_type)) > 0
         AND source_id IS NOT NULL AND length(btrim(source_id)) > 0
         AND source_version IS NOT NULL AND source_version > 0)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS graph_entities_source_version_uidx
    ON graph_entities (tenant_id, source_domain, source_type, source_id, source_version)
    WHERE source_domain IS NOT NULL;
CREATE INDEX IF NOT EXISTS graph_entities_tenant_type_name_idx
    ON graph_entities (tenant_id, entity_type, lower(entity_name) text_pattern_ops);
CREATE INDEX IF NOT EXISTS graph_entities_tenant_project_idx
    ON graph_entities (tenant_id, project_id, entity_type);
CREATE INDEX IF NOT EXISTS graph_entities_attributes_gin_idx
    ON graph_entities USING gin (attributes jsonb_path_ops);

CREATE TABLE IF NOT EXISTS graph_relations (
    tenant_id text NOT NULL,
    relation_id text NOT NULL,
    relation_type text NOT NULL,
    source_entity_id text NOT NULL,
    target_entity_id text NOT NULL,
    source_entity_type text NOT NULL,
    target_entity_type text NOT NULL,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    project_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, relation_id),
    FOREIGN KEY (tenant_id, source_entity_id)
        REFERENCES graph_entities (tenant_id, entity_id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, target_entity_id)
        REFERENCES graph_entities (tenant_id, entity_id) ON DELETE CASCADE,
    CHECK (length(btrim(relation_id)) BETWEEN 1 AND 200),
    CHECK (length(btrim(relation_type)) BETWEEN 1 AND 63),
    CHECK (source_entity_id <> target_entity_id OR source_entity_type = target_entity_type),
    CHECK (jsonb_typeof(attributes) = 'object')
);

CREATE INDEX IF NOT EXISTS graph_relations_tenant_source_idx
    ON graph_relations (tenant_id, source_entity_id, relation_type, created_at);
CREATE INDEX IF NOT EXISTS graph_relations_tenant_target_idx
    ON graph_relations (tenant_id, target_entity_id, relation_type, created_at);
CREATE INDEX IF NOT EXISTS graph_relations_tenant_project_idx
    ON graph_relations (tenant_id, project_id, relation_type);
CREATE INDEX IF NOT EXISTS graph_relations_attributes_gin_idx
    ON graph_relations USING gin (attributes jsonb_path_ops);

COMMIT;
