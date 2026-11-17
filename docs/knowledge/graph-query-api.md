# Graph query API and graph-first retrieval

C-22-04 adds a tenant-bound query facade over the graph repository contract. It provides bounded neighbor expansion, shortest directed paths, ordered relation-chain templates and graph-first document candidate narrowing for hybrid retrieval.

## Query operations

- `neighbors`: breadth-first expansion in incoming, outgoing or both directions; optional relation-type filters; depth is limited to 5 and result entities/relations to 500.
- `shortest_path`: directed breadth-first shortest path with a maximum depth of 8 and a 5,000-node visit budget.
- `match_relation_chain`: ordered relation-type path template, up to 8 hops and 500 results per hop.\n- `match_subgraph`: bounded induced-subgraph match over 1-50 explicit entity IDs, with optional relation-type filters and connectedness requirement; only internal edges between authorized nodes are returned.
- `hybrid_retrieve`: search graph entity names first, derive candidate Knowledge document IDs from source references, then invoke a vector-capable ACL-aware document retriever to vector-rerank only those candidate documents and re-check canonical document/chunk permissions before returning hits.

Every operation requires a repository explicitly bound to the service tenant, validates returned entity/relation tenant and project scope as defense in depth, and passes the caller-authorized project allow-list to the repository. The PostgreSQL repository remains responsible for tenant predicates and project filters. Query timeouts are enforced with cancellation; invalid limits, directions, depths, relation-filter counts, query lengths and empty identifiers fail closed. Neighbor results deduplicate entity/relation IDs, re-authorize every discovered node before returning it, and report truncation when the result budget is reached. Subgraph matching reloads every requested entity under the same tenant/project scope and rejects missing or unauthorized nodes; edge results are restricted to endpoints in the requested node set.

## Authorization boundary

The graph is a derived index, not an authorization authority. A graph hit or source reference is not sufficient permission to disclose canonical Knowledge content. The hybrid retriever contract requires a fresh ACL check against the canonical document/chunk store, and returned hits are additionally restricted to the graph-derived candidate document IDs. Hosts must derive `allowed_project_ids` from the authenticated principal; never accept an arbitrary client-provided project list.

## Operational limits

The service defaults to a 2-second query timeout (configurable up to 30 seconds). Hybrid query text is capped at 200 characters to match the PostgreSQL entity-search contract. Neighbor depth is capped at 5, shortest path depth at 8, relation-chain length at 8, subgraph size at 50 nodes, candidate seed count at 200, and output limits at 500. Timeouts propagate as `asyncio.TimeoutError`; callers should translate them into the platform's standard timeout response. No unbounded graph traversal or arbitrary client-provided SQL/DSL is exposed.

## Integration boundary

The facade consumes the tenant-bound `get_entity`, `list_relations` and `search_entities` repository contract. PostgreSQL storage is delivered separately under C-22-02; production composition must inject that repository and an ACL-aware Knowledge retriever. This PR does not claim the graph repository or retriever has been wired into a production API server.
