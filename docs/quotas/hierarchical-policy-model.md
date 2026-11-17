# Hierarchical quota policy model

## Resource dimensions
Policies can independently constrain tokens, input/output tokens, USD cost,
concurrency, storage bytes, requests, tool calls and runtime milliseconds. Values
are finite, non-negative numbers; unknown dimensions are rejected.

## Scope and precedence
A policy applies to one of four scopes: platform default (`*`), tenant, project,
or subject (an authenticated principal/service identity). Resolution is tenant
isolated. For each resource dimension, the most specific applicable scope wins:
subject > project > tenant > platform default. Dimensions not set at a more
specific scope inherit from the nearest parent policy. Within one scope,
higher numeric priority wins; version and policy ID provide deterministic
tie-breaking.

## Defaults and overrides
Platform defaults are explicit versioned policies. Tenant policies override only
the dimensions they specify; project and subject policies can further override
those dimensions without erasing inherited limits for other resources. Disabled
policies are ignored. Resolution returns each effective value with its source
policy ID and version for explainability.

## Configuration and audit
Policy writes require quota-management authorization. Project and subject writes
also require resource-scope authorization. Platform defaults require a separate
platform-level permission and cannot be written by an ordinary tenant quota
manager. Each create/update records actor, tenant, policy ID/version, before and
after snapshots, UTC timestamp and SHA-256 digest in an append-only audit table.
The PostgreSQL adapter commits policy and audit rows in one transaction.

## API
- `POST /api/platform/v1/quotas/policies` creates or updates a policy.
- `GET /api/platform/v1/quotas/policies/effective` resolves the effective limits
  for the authenticated tenant and optional authorized project/subject.
