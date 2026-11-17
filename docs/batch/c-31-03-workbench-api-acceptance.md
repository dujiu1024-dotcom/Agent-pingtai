# C-31-03 — Batch Workbench API acceptance

The batch API is mounted only when the host supplies `batch_service_resolver`.
It uses the authenticated request principal and the existing workbench task
authorizer; it does not create a second execution runtime.

## Contract

- `POST /api/platform/v1/workbench/batches`: submit a template and finite
  dataset, returning HTTP 202. Tenant and actor are taken from the authenticated
  principal, never from request JSON.
- `GET /api/platform/v1/workbench/batches` and
  `GET /api/platform/v1/workbench/batches/{batch_id}`: list/query within the
  resolved tenant.
- `POST /{batch_id}/cancel`: request cancellation through the host service.
- `POST /{batch_id}/retry`: retry through the host service and require a
  caller-provided idempotency key.

The router rejects a resolver whose service tenant differs from the principal,
checks create/execute/read/cancel/retry authorization, bounds dataset size and
concurrency, and returns 404 for absent batches.

## Automated coverage

Run:

```bash
python -m unittest tests.test_issue_31_c_31_03_batch_workbench_api -v
```

The acceptance test exercises the submit → list → query → cancel → retry API
flow, principal-derived tenant/actor propagation, tenant mismatch rejection,
authorization denial, missing resources, invalid datasets, boolean concurrency
values, and mandatory retry idempotency.

The test uses a deterministic service double. It proves the HTTP contract and
composition boundary, not durable worker execution, PostgreSQL persistence,
real Agent runtime execution, or production cancellation latency.
