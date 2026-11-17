# Issue #666 / S-33-01 — tenant-aware canary routing

## Contract

The host gateway wraps the new and legacy ASGI applications with
`CutoverGateway`. A trusted `tenant_resolver` must obtain the tenant from
authenticated host state or independently validate the credential. Request
headers and query parameters are never accepted as tenant identity.

Routing order:

1. If cutover is disabled, route to legacy.
2. If the authenticated tenant is on the allowlist, route to the new platform.
3. Otherwise, route to the new platform when the tenant's deterministic HMAC
   bucket is below the configured percentage; all other tenants remain on legacy.
4. If tenant resolution fails, return 503 and dispatch to neither application.

The HMAC assignment is tenant-stable across requests and process restarts.
The same `TenantCutoverPolicy.use_new_system` decision is exposed to Workflow
entry points through `select_workflow_runtime`, preventing API/Workflow cohort
drift. Percentage is validated as an integer from 0 through 100; boolean values
are rejected.

## Deployment settings

- `AGENT_PLATFORM_CUTOVER_ENABLED`: `true` or `false`, default `false`.
- `AGENT_PLATFORM_CUTOVER_TENANTS`: comma-separated tenant IDs, default empty.
- `AGENT_PLATFORM_CUTOVER_PERCENTAGE`: integer 0–100, default 0.
- `AGENT_PLATFORM_CUTOVER_SALT`: deployment secret (at least 16 UTF-8 bytes),
  required when percentage is non-zero.

Load settings with `TenantCutoverPolicy.from_env()`; invalid settings fail startup.
Keep the salt stable while a rollout is in progress. Rotating it reshuffles
percentage cohorts and should be treated as a new rollout.

## Integration

The deployment gateway owns both live application objects and injects the
authenticated tenant resolver:

```python
from agent_platform.switching import CutoverGateway, TenantCutoverPolicy

gateway = CutoverGateway(
    new_app=platform_app,
    legacy_app=legacy_app,
    policy=TenantCutoverPolicy.from_env(),
    tenant_resolver=resolve_authenticated_tenant,
)
```

The Workflow host uses `select_workflow_runtime(tenant_id, policy, ...)` at
the workflow dispatch boundary with the same tenant ID and policy.

## Verification

Run:

```bash
python -m unittest tests.test_issue_666_s_33_01_canary_routing -v
ruff check agent_platform/switching tests/test_issue_666_s_33_01_canary_routing.py
```

The focused suite covers allowlist/percentage boundaries, stable cohort
assignment, environment validation, Workflow parity, spoofed-header rejection,
ASGI fallback, resolver outage behavior, and lifespan handling.

## Acceptance boundary

This is the deployable routing primitive and Workflow decision contract. It
does not claim that a production gateway has been configured, that traffic has
been shifted, or that a real legacy/new deployment has been exercised. Those
require the host deployment to inject both applications and the authenticated
tenant resolver, then run a separately authorized field canary.
