-- Tenant-scoped graph records with indexed entity lookup and adjacency traversal.
BEGIN;

CREATE TABLE IF NOT EXISTS platform_graph_entities (
    tenant_id       text NOT NULL REFERENCES platform_tenants(tenant_id) ON DELETE CASCADE,
    entity_id       text NOT NULL,
    entity_type     text NOT NULL,
    project_id      text,
    attributes      jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_domain   text,
    source_type     text,
    source_id       text,
    source_version  text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, entity_id),
    CHECK (jsonb_typeof(attributes) = 'object'),
    CHECK ((source_domain IS NULL AND source_type IS NULL AND source_id IS NULL)
        OR (source_domain IS NOT NULL AND source_type IS NOT NULL AND source_id IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_graph_entities_type
    ON platform_graph_entities (tenant_id, entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_graph_entities_project_type
    ON platform_graph_entities (tenant_id, project_id, entity_type, entity_id)
    WHERE project_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_graph_entities_source
    ON platform_graph_entities (tenant_id, source_domain, source_type, source_id, source_version)
    WHERE source_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS platform_graph_relations (
    tenant_id           text NOT NULL REFERENCES platform_tenants(tenant_id) ON DELETE CASCADE,
    relation_id         text NOT NULL,
    relation_type       text NOT NULL,
    source_entity_id    text NOT NULL,
    target_entity_id    text NOT NULL,
    project_id          text,
    attributes          jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, relation_id),
    FOREIGN KEY (tenant_id, source_entity_id)
        REFERENCES platform_graph_entities(tenant_id, entity_id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, target_entity_id)
        REFERENCES platform_graph_entities(tenant_id, entity_id) ON DELETE CASCADE,
    CHECK (jsonb_typeof(attributes) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_graph_relations_source
    ON platform_graph_relations (tenant_id, source_entity_id, relation_type, target_entity_id);
CREATE INDEX IF NOT EXISTS idx_graph_relations_target
    ON platform_graph_relations (tenant_id, target_entity_id, relation_type, source_entity_id);
CREATE INDEX IF NOT EXISTS idx_graph_relations_project
    ON platform_graph_relations (tenant_id, project_id, relation_type)
    WHERE project_id IS NOT NULL;

COMMIT;
