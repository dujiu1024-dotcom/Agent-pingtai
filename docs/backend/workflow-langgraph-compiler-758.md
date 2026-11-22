# Issue #758 — Workflow LangGraph compiler and diagnostics API

## API contract

The backend reads an immutable tenant-scoped `workflow` Registry version. The request body cannot replace the stored definition or inject executable code.

- `GET /api/platform/v1/workflows/{workflow_id}/versions/{version}/diagnostics`
- `POST /api/platform/v1/workflows/{workflow_id}/versions/{version}/compile`

Both endpoints require the workflow `compile` authorization action. Tenant identity comes from the authenticated Principal and the registry repository tenant is checked before loading a version. Missing versions return 404, denied access 403, invalid graph definitions 422 with path-addressed diagnostics. The existing Registry `publish` endpoint is also gated: it compiles the exact draft version first and refuses publication when the compiler is not configured or any diagnostic fails.

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

The compiler accepts both canonical schema v1 and the current visual-designer payload (nodes + typed edges with one start node). It validates declared source/target ports and converts condition-node output ports into LangGraph conditional edges. Start/end are structural markers; parallel fan-out and join fan-in remain LangGraph graph topology. Registered node retry metadata maps to LangGraph `RetryPolicy`. The compiler builds a LangGraph `StateGraph` and calls `compile()`. It does not invoke/stream the graph, start a Run, own a scheduler, create a checkpointer, or implement interrupt/resume. Run execution and durable checkpoints remain in the existing LangGraph runtime.

## Compiled artifact identity

The compile response pins `workflow_id`, Registry `version`, canonical definition SHA-256, node/edge counts, and `engine=langgraph`. It explicitly reports `execution_started=false`. A compile response is diagnostic evidence, not a published Run or durable compiled-artifact store. Runtime admission must continue to pin the Registry version and use the platform's configured LangGraph executor.

## Composition

Pass `workflow_compiler_resolver(principal)` to `create_platform_app` alongside the existing `registry_service_resolver` and `registry_authorizer`. The resolver must return a tenant-configured `LangGraphWorkflowCompiler` with reviewed node factories registered for all executable node types used by the designer. The same tenant-scoped Registry service is reused; there is no parallel workflow persistence layer.

## Validation

Run `python -m unittest tests.test_workflow_definition_compiler tests.test_workflow_compiler_api tests.test_workflow_publish_compile_gate -v`. The tests cover allowlisted node factories, current designer payload compatibility, schema/typed-port compatibility, unknown nodes, graph references, branch targets, retry/permission metadata, compiler failures, API authorization, and fail-closed publication. Real LangGraph compile integration is separately exercised when LangGraph dependencies are installed; no real server/device is required.


## Production composition root wiring

`agent_platform.production.create_production_app` forwards the compiler resolver
into `create_platform_app`, alongside the tenant-scoped Registry resolver and
authorizer. A deployment may pass `workflow_compiler_resolver` directly, or
install exactly one reviewed Python entry point named `resolver` in the
`agent_platform.workflow_compiler` entry-point group. The loaded callable must
return the tenant-specific `LangGraphWorkflowCompiler` for the authenticated
Principal and must use only reviewed node factories.

When no trusted resolver is installed, production still mounts the diagnostics,
compile, and Registry publish routes, but the resolver returns HTTP 503. This is
intentional fail-closed behavior: no empty/fake node registry is treated as a
working compiler, and Workflow publication remains disabled until the trusted
node factories are configured. Multiple installed resolver entry points or a
non-callable entry point fail application composition rather than selecting an
ambiguous implementation.
