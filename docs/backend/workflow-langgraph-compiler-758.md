# Issue #758 — Workflow LangGraph compiler and diagnostics API

## API contract

The backend reads an immutable tenant-scoped `workflow` Registry version. The request body cannot replace the stored definition or inject executable code.

- `GET /api/platform/v1/workflows/{workflow_id}/versions/{version}/diagnostics`
- `POST /api/platform/v1/workflows/{workflow_id}/versions/{version}/compile`

Both endpoints require the workflow `compile` authorization action. Tenant identity comes from the authenticated Principal and the registry repository tenant is checked before loading a version. Missing versions return 404, denied access 403, invalid graph definitions 422 with path-addressed diagnostics.

## Definition schema v1

```json
{
  "schema_version": 1,
  "entry_point": "agent",
  "nodes": [{
    "id": "agent",
    "node_type": "agent",
    "version": 1,
    "config": {},
    "config_schema": {"type": "object"},
    "input_schema": {"type": "object"},
    "output_schema": {"type": "object"},
    "ports": {"inputs": ["default"], "outputs": ["default"]},
    "permissions": ["agent.execute"],
    "risk": "medium",
    "error_retry": {"max_attempts": 2}
  }],
  "edges": [{"source": "agent", "target": "__end__"}],
  "branches": []
}
```

Node implementations are resolved only through an injected `WorkflowNodeRegistry` of reviewed factories. Unknown type/version, schema drift, permission/risk/retry metadata drift, duplicate IDs, invalid references, unsupported schema version, or invalid branch targets block compilation. Definitions cannot provide Python import paths, source code, arbitrary callables, scheduler or checkpointer objects.

The compiler builds a LangGraph `StateGraph` and calls `compile()`. It does not invoke/stream the graph, start a Run, own a scheduler, create a checkpointer, or implement interrupt/resume. Run execution and durable checkpoints remain in the existing LangGraph runtime.

## Compiled artifact identity

The compile response pins `workflow_id`, Registry `version`, canonical definition SHA-256, node/edge counts, and `engine=langgraph`. It explicitly reports `execution_started=false`. A compile response is diagnostic evidence, not a published Run or durable compiled-artifact store. Runtime admission must continue to pin the Registry version and use the platform's configured LangGraph executor.

## Composition

Pass `workflow_compiler_resolver(principal)` to `create_platform_app` alongside the existing `registry_service_resolver` and `registry_authorizer`. The same tenant-scoped Registry service is reused; there is no parallel workflow persistence layer.

## Validation

Run `python -m unittest tests.test_workflow_definition_compiler -v`. The tests cover allowlisted node factories, schema compatibility, unknown nodes, graph references, branch targets, permission metadata, and compiler failures. API tests cover tenant scope and authorization. Real LangGraph compile integration is separately exercised when LangGraph dependencies are installed; no real server/device is required.
