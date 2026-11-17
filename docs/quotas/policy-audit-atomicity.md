# Quota policy configuration and audit atomicity

Quota policy mutations are an audited control-plane operation. A policy revision is
accepted only when its policy row and the corresponding append-only audit row commit
in the same PostgreSQL transaction.

## Write contract

- The service authorizes the requested scope before constructing the audit record.
- Policy versions are monotonic. PostgreSQL applies an upsert only when the incoming
  version is greater than the stored version.
- A stale or duplicate version is rejected and produces no audit event.
- The policy write and audit insert run in one database transaction. If the audit
  insert fails, PostgreSQL rolls back the policy write.
- A repository that cannot implement the atomic write contract is rejected; the
  service does not fall back to two independent writes.

## Scope resolution

Policies inherit from default to tenant, project, and subject scopes. For each
resource dimension, the most specific enabled policy wins. Within one scope,
higher priority wins, followed by version and policy ID as deterministic tie-breakers.
A policy may override only the resource dimensions it declares; other dimensions
continue to inherit from parent scopes.

## Verification

`tests/test_quota_policies.py` covers inheritance, priority, tenant isolation,
authorization, and PostgreSQL repository transaction behavior. The PostgreSQL
atomicity tests verify that an audit failure rolls back the policy mutation and
that a stale version cannot append an audit record.

These automated tests do not replace verification against a deployed PostgreSQL
instance or production audit sink.
