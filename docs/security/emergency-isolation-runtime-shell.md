# Emergency isolation and runtime safety shell

## Behavior

- Isolation scopes are global, tenant, and Run. Global applies to every execution; tenant and Run scopes match only their exact server-side identifiers.
- The control-plane provider is the only source of isolation state. Agent arguments cannot set, clear, or override an isolation record.
- Expired records stop matching. Records with invalid timestamps are rejected at construction.
- The runtime shell rejects stale snapshots, unavailable control-plane reads, and audit failures (fail closed).
- An isolation match is audited with scope, target, reason, incident ID, tenant, Run, actor, and tool identity before returning a permission error. The wrapped handler is not invoked.
- Allowed executions receive authorization and completion/failure audit events. Audit failure prevents handler invocation; a completion-audit failure propagates to the caller.

## Composition

Wrap the final Gateway contextual handler with `RuntimeSafetyShell`, supplying a trusted `IsolationStateProvider` and mandatory `IsolationAuditSink`. Keep state mutation behind an authenticated control-plane API with tenant-scoped authorization and explicit incident metadata. The state provider should return a versioned snapshot and an observation timestamp; default maximum age is 30 seconds.

This is a platform boundary component, not a replacement for the OCI sandbox, Tool Gateway authorization, approval checks, or Run cancellation registry. Wire it into the production handler composition and control-plane persistence before enabling emergency isolation operationally. No real host, container daemon, or production control plane was exercised in this change.
