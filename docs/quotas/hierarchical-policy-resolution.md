# Hierarchical quota policy resolution

Quota policy resolution is resource-dimension based. Enabled persisted policies
are considered in this precedence order:

1. subject
2. project
3. tenant
4. persisted platform default

Within one scope level, higher numeric priority wins; policy version and policy
ID provide deterministic tie-breaking. A policy overrides only the dimensions
it explicitly contains. Other dimensions continue to inherit from less
specific policies.

The in-memory adapter also accepts validated static defaults. These are the
last-resort fallback for dimensions not configured by any persisted policy;
they are identified in resolution metadata as `system-default` at version 0.
They do not override persisted defaults or scoped policies. Production
PostgreSQL deployments should represent platform defaults as persisted
`scope_type=default` policy rows so their changes are versioned and audited.

Configuration changes require an authorized actor and an atomic policy +
audit write. A stale version or audit-store failure rejects the change.
