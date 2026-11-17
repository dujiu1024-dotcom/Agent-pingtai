# Issue #614 — Production Run execution composition

## What this change wires

build_production_run_execution() composes the existing repository-owned pieces:

- RegistryBackedTaskCenterService for tenant-bound task submission and immutable Run registry snapshots.
- RegistryLangGraphExecutorResolver (provided as a trusted callable) for validating the Run-pinned Agent/template/tool/Skill snapshot and creating LangGraphRunExecutor.
- RunExecutionWorker for durable PostgreSQL task claiming, worker lease heartbeat/recovery, shared queue/worker-pool quota slots, execution and output persistence.
- create_platform_app() lifecycle hooks to start/stop the task worker and enable Workbench task APIs and Run executor routes.

create_production_app() accepts a run_execution_builder callback. It invokes the callback only after constructing the production PostgreSQL Run store, lifecycle manager and quota services, then passes those exact shared objects to the callback. The returned ProductionRunExecution must reference the same Run store instance. This prevents a split-brain where the API submits tasks to one store while the worker claims from another.

## Trusted deployment boundary

The callback is ordinary trusted Python supplied by an installed deployment package through the single Python entry point group `agent_platform.production_run_execution` with entry name `builder`; it is not an Agent-authored import string or environment-controlled module path. The production Compose overlay sets `AGENT_PLATFORM_REQUIRE_RUN_EXECUTION=true` by default, so startup fails if the deployment package does not install exactly one builder. The callback must construct the approved runtime dependencies and return build_production_run_execution(...) with:

- a tenant-bound registry resolver and Run snapshot resolver;
- a RegistryLangGraphExecutorResolver backed by the approved RegistryRuntimeAssembler, Vault/secret resolver, budget-enforcing model router, reviewed Skill store and trusted Tool handler catalog;
- the shared quota concurrency limiter, queue scope resolver and unique worker ID;
- a server-side task authorizer derived from the authenticated Principal.

The helper rejects a missing executor resolver, task authorizer, queue scope resolver, quota limiter, or a lifecycle that does not share the exact durable Run store. It does not create an empty Tool catalog, bypass budget/audit enforcement, trust Agent-supplied executable code, or invent a default graph.

## Example deployment composition

The deployment-owned package should register a trusted builder in its package metadata and can also pass it directly to create_production_app for non-Compose deployments:

```python
from uuid import uuid4

from agent_platform.production import create_production_app
from agent_platform.runtime.production_composition import build_production_run_execution

def build_run_execution(*, run_store, lifecycle, quota_service,
                        quota_store, identity_service, identity_repository,
                        audit_writer, connection_factory, redis_client, settings):
    # These constructors belong to the reviewed deployment integration package.
    # They must use the approved Vault secret resolver, model router, reviewed
    # Skill dependency store and trusted Tool handler catalog.
    snapshots, registry_for_principal = build_registry_ports(
        connection_factory=connection_factory,
    )
    executor_resolver = build_approved_executor_resolver(
        snapshots=snapshots,
        lifecycle=lifecycle,
        quota_service=quota_service,
        quota_store=quota_store,
        settings=settings,
    )
    return build_production_run_execution(
        run_store=run_store,
        lifecycle=lifecycle,
        registry_resolver=registry_for_principal,
        snapshot_resolver=lambda _principal: snapshots,
        executor_resolver=executor_resolver,
        task_authorizer=build_task_authorizer(identity_service),
        concurrency_limiter=build_shared_quota_limiter(quota_service),
        worker_pool_scope_id="production",
        queue_scope_resolver=resolve_queue_scope,
        worker_id=uuid4().hex,
    )

app = create_production_app(run_execution_builder=build_run_execution)\n\n# For the default agent_platform.production:app entrypoint, register the same\n# callable as the 'builder' entry in group\n# 'agent_platform.production_run_execution'.
```

The build_registry_ports, build_approved_executor_resolver, quota limiter and authorization functions above are deployment integration functions, not repository defaults. They must be implemented using the approved services and reviewed Tool catalog; this repository intentionally does not guess production secret references, provider credentials, executable tools or model graph semantics.

## Lifecycle and verification

When a ProductionRunExecution is supplied, the Workbench task service, Run executor API resolver and RunExecutionWorker are installed together. The worker is started/stopped by the existing FastAPI application lifecycle. Each ASGI worker process must receive a unique worker ID and use shared PostgreSQL/Redis-backed quota dependencies.

Focused tests cover composition, tenant-bound task service resolution, exact shared Run store identity, mandatory quota/authorization/executor ports and worker start/stop. They do not prove live Vault, model Provider, PostgreSQL checkpoint migration, production Tool handlers or multi-process execution. Those require deployment evidence and remain separate acceptance items.
