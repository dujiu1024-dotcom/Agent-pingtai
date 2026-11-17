# Graph query and hybrid retrieval contract (C-22-04)

## Query DSL

`GraphQuerySpec` accepts only:
- `mode`: `neighbors`, `paths`, or `subgraph`.
- `start_entity_ids`: 1-20 canonical entity IDs.
- `relation_types` / `entity_types`: optional bounded type filters.
- `max_depth`: 1-5.
- `limit`: 1-100.
- `timeout_ms`: 100-5000.

Unknown fields (especially caller-supplied tenant, project or user identity) are
rejected. The authenticated `KnowledgePrincipal` supplies tenant and project
scope; the query payload cannot widen it. A repository adapter implements the
mode-specific traversal, including bounded path/shortest-path templates and
subgraph expansion, behind `GraphQueryRepository.execute_query`.

## Authorization and resource bounds

The service passes tenant ID and the principal's readable/manageable project set
to the adapter. It independently verifies every returned record, including every
intermediate node in path results. Cross-tenant or unauthorized-project rows fail
closed. Results are deduplicated by entity/result ID and truncated to the requested
limit. The adapter must apply the same predicates inside the database query; the
service checks are defense in depth, not a substitute for storage-level ACLs.

Each graph query is wrapped in `asyncio.wait_for`; hybrid retrieval shares one
total time budget between graph narrowing and vector retrieval. Cancellation is
propagated to the adapter. Limits and timeouts are bounded again by service
configuration.

## Hybrid graph + vector retrieval

`hybrid_search` first obtains authorized graph candidates, then passes only
those candidate entity IDs to the vector retriever. Vector results are checked
again for tenant/project scope and membership in the graph candidate set.
Reciprocal-rank fusion combines graph and vector ranks without comparing raw
scores from different providers. Duplicate entities are merged into one result.

If graph narrowing returns no authorized candidates, vector retrieval is not
run as an unbounded fallback. This avoids silently broadening the retrieval
scope when the graph layer has no evidence.

## Adapter and acceptance boundary

The service is storage/provider agnostic. A Postgres/Neo4j adapter can implement
the query protocol separately; the repository must support tenant/project
predicates, depth/row limits, cancellation or server-side statement timeout,
and stable entity/result IDs. Unit tests use fake adapters and do not establish
the 500 ms production performance target. Production acceptance still requires
representative graph-size benchmarks and authorization tests.
