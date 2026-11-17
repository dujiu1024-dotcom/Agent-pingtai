# Knowledge graph operations workbench

Issue #517 / C-22-06 adds a graph operations view to the authenticated platform workbench.

## Capabilities

- Search entities by name prefix and render the authorized result set as an SVG relationship graph.
- Expand one entity to a bounded, depth-limited neighborhood. The graph query service revalidates tenant and project scope for each returned node and edge.
- Display sample-scoped graph quality indicators: entity/relation counts, isolated-node ratio, relation density and connected-component count.
- Submit manual attribute corrections with an expected entity version. The existing GraphGovernanceService validates the authenticated KnowledgePrincipal, schema and optimistic version before recording the correction.

## API wiring

The host may configure `graph_query_service_resolver` and `graph_read_authorizer` in `create_platform_app`. The resolver must return a tenant-bound GraphQueryService; the authorizer returns the project IDs the principal may read. Both are required together. The optional `graph_governance_resolver` enables the annotation endpoint; it must return the tenant's GraphGovernanceService. The host identity repository supplies the KnowledgePrincipal used by graph governance.

Routes:

- `GET /api/platform/v1/graph/entities?q=...`
- `GET /api/platform/v1/graph/entities/{entity_id}/neighbors?direction=both&depth=1&limit=100`
- `GET /api/platform/v1/graph/metrics?q=...&limit=100`
- `PATCH /api/platform/v1/graph/entities/{entity_id}` with `expected_version` and `attributes`

## Metric semantics and limits

Metrics are explicitly scoped to the authorized name-prefix search sample (maximum 100 entities), not the entire tenant graph. Relations are counted only when both endpoints are in that sample. A result reaching the sample limit is marked possibly truncated. This avoids presenting a partial browse result as a global graph-quality measurement. Neighbor traversal remains bounded by the GraphQueryService depth, result and timeout limits.

The view is capability-gated by the server's `graph` feature. UI gating is presentation only; tenant/project checks and governance authorization remain server-side. No third-party visualization runtime or client-supplied tenant identity is used.
