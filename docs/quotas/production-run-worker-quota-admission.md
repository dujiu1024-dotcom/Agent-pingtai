# Issue #17 / A-17-03 — production Run worker quota admission

## Composition contract

The production ASGI root exposes `app.state.run_execution_worker_factory`.
It is a partial application pinned to the exact `PostgresRunStateStore`,
`QuotaService` and `RunLifecycleManager` created by
`agent_platform.production:create_production_app()`.

The deployment-owned worker host supplies only trusted runtime dependencies:

- `executor_resolver`: registry-backed executor resolver; never request data.
- `worker_id`: stable identity for this worker process.
- `worker_pool_scope_id`: configured shared worker-pool quota scope.
- `queue_scope_resolver`: maps a persisted task record to its queue quota scope.

Example after importing the production ASGI app in a configured worker process:

    from agent_platform.production import app

    worker = app.state.run_execution_worker_factory(
        executor_resolver=trusted_executor_resolver,
        worker_id="run-worker-01",
        worker_pool_scope_id="production-default",
        queue_scope_resolver=lambda record: str(record["queue_id"]),
    )
    await worker.start()

The factory refuses a process-local `InMemoryQuotaStore`; it requires the
shared `PostgresQuotaStore`. It constructs `QuotaConcurrencyLimiter` using
the production `QuotaService` and passes it to `RunExecutionWorker`.
The worker acquires both tenant queue and shared worker-pool slots before
resolving the executor. Quota denial therefore occurs before graph/provider
construction. Run lease heartbeat remains active while waiting for a slot.

## Validation

`tests/test_issue_17_a_17_03_run_worker_quota_factory.py` verifies that the
factory preserves the production Run store, lifecycle resolver and executor
resolver, binds the limiter to the exact shared quota service, and rejects
process-local quota storage without opening a database connection.

## Explicit acceptance boundary

This is a reusable production worker composition entrypoint, not proof that a
deployment has started the worker. The production host still owns worker
process startup, trusted executor/runtime provider construction, queue scope
mapping and worker-pool policy provisioning.

This change enforces queue and worker-pool concurrency admission. It does not
automatically wrap every model/provider call in `QuotaEnforcedHarnessAdapter`,
supply request/cost estimators, configure price-feed or storage measurement
providers, deliver quota warnings, or prove multi-process/production behavior.
Those remain separate A-17-03 acceptance gaps until wired and evidenced.
