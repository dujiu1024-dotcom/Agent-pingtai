# C-15-02 Approval policy binding

Approval requests are immutable authorizations for one exact action, resource,
actor, Run, parameter digest, and trusted policy revision.

## Policy revision

- Configure `ApprovalService(policy_version=...)` from trusted server-side
  composition. Do not accept policy versions from HTTP clients or Agent input.
- Configure each `ToolDefinition(policy_version=...)` with the same revision.
- Bump the revision whenever action risk classification, permission rules,
  resource resolution/authorization, input schema, or approval semantics change.
- PostgreSQL consumption atomically matches tenant, approval id, action digest,
  policy revision, approved state, and expiry. A stale revision cannot execute.
- The revision defaults to `1` for compatibility; production composition must
  deliberately advance it on policy changes.

## Approver authorization

The approval router requires a trusted
`authorize_approver(principal, action, resource)` callback. Pending workbench
items are filtered per action/resource; detail, decision, and administrative
revocation re-check the same scoped authorization. Requesters cannot approve
their own requests. The callback must derive tenant/resource grants from trusted
identity/RBAC context.

The approval API also requires a trusted `resolve_risk_level(principal, action,
resource)` callback. Client-supplied risk levels are ignored; classification
must come from the server-side action registry/policy.

## Fail-closed behavior

Unknown actions remain denied by the trusted tool registry. Missing verifier,
verifier/database errors, expired/revoked/rejected/consumed approvals, digest
mismatches, Run mismatches, and policy revision mismatches prevent tool handler
invocation. The gateway consumes approval before invoking the handler and
writes a durable started audit before side effects.

## Validation boundary

Unit/API tests cover action/resource-scoped approver authorization and policy
revision invalidation. PostgreSQL migration and verifier behavior are exercised
by CI integration checks. No production server or external resource is used.
