# Audit immutability and fail-closed operating policy

This policy applies to `platform_tool_audit` and `platform_identity_audit`.
It complements the append-only triggers installed by
`agent_platform/storage/postgres/015_audit_append_only.sql`.

## Mutation boundary

- Agent and user-facing APIs expose audit events through authorized **GET-only**
  routes. There is no Agent-facing create, update, delete, or truncate endpoint.
- Runtime code may append through the host-owned audit writer. It must not issue
  UPDATE, DELETE, or TRUNCATE against either audit table.
- Apply schema migrations with a dedicated migration/owner principal. The
  application/runtime database principal **must not own** either audit table,
  own the trigger function, or hold DDL/TRIGGER privileges.
- Grant the runtime principal only the table privileges it needs: `SELECT`
  and `INSERT`. Revoke UPDATE, DELETE, and TRUNCATE, and remove inherited
  grants through role memberships as well as direct grants. The migration's
  `REVOKE ... FROM PUBLIC` is a baseline, not a substitute for checking
  direct grants to deployment-specific roles.
- A table owner or PostgreSQL superuser can bypass ordinary application
  protections. Those principals are privileged infrastructure operators, not
  Agent identities, and must be isolated from the application process and
  credentials. The append-only guarantee is against ordinary Agent/runtime
  principals, not a claim of tamper-proof storage against a database superuser.

For deployment integration checks, set
`AGENT_PLATFORM_AUDIT_RUNTIME_ROLE` to the actual application DB role. The
PostgreSQL integration test verifies that it is not the table owner, can read
and append, and cannot update, delete, or truncate either audit table.

## Failure-closed decision table

| Boundary | Required behavior when the audit store is unavailable |
| --- | --- |
| Authenticated request intent / write-ahead event | Fail closed: do not enter the application handler or start the protected side effect. Propagate the audit error. |
| Tool, model, approval, credential, or administrative side effect | Persist the required `started`/intent event before dispatch. If that write fails, do not dispatch. |
| Completion/outcome event after a side effect | Do not silently swallow the failure. Surface an operational error and mark the operation as outcome-unknown for reconciliation. Do **not** automatically replay a non-idempotent side effect just to obtain an audit record. |
| Audit query | Fail closed: do not return unscoped or cached fallback data when authorization or the configured reader is unavailable. |
| Missing required sink or actor resolver | Refuse to enable the protected feature / fail application composition. Never replace it with an in-memory sink in production. |

A successful write-ahead event proves that the attempt was authorized for
dispatch; it does not prove the final outcome. A missing completion event is
therefore an explicit reconciliation condition, not permission to repeat the
external action. Retry is allowed only when the operation's own idempotency
contract proves that repeating it cannot duplicate the effect.

## Verification boundary

Automated tests cover GET-only query routes, PostgreSQL mutation triggers,
runtime-role privilege checks (when the deployment role is supplied), sink
error propagation, and request write-ahead failure preventing handler entry.
The completion-event test verifies that the error is surfaced and the handler
is not automatically invoked a second time.

A production database role/ownership review and live audit-store outage drill
remain deployment evidence. Unit tests and mocked sinks do not replace those
checks.