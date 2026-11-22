# Issue #889 / L2-T11 — Hybrid deployment offline autonomy

## Scope and reuse boundary

This increment implements the deterministic offline authorization/license decision
boundary and documents the dependency behavior matrix. It reuses the existing
identity, policy, secret, model-routing, task/Run, audit and disaster-recovery
services; it does not recreate their stores, task queues, backup orchestration or
distributed failover. The evaluator is a local policy decision primitive. The
host must inject verified snapshots, a trusted clock and a durable local audit
writer. Signature verification and snapshot distribution remain the responsibility
of the existing identity/control-plane adapter.

Code: `agent_platform/resilience/offline_policy.py`.

## Offline policy defaults and hard limits

| Control | Default | Rule |
|---|---:|---|
| Cached authorization/policy freshness | 1 hour | Measured from last successful revocation/policy sync; stale snapshot is denied |
| Revocation propagation target / hard maximum | 15 minutes | Once last successful revocation sync is older than this, cached grants are denied |
| License grace period | 72 hours | Grace is not a license renewal; it permits read and resumable internal work only |
| Local audit | Required | If the local audit sink is absent or write fails, deny the operation |

The two authorization limits are independent: the effective offline authorization
window is the shorter of cache freshness and revocation-delay limits. A deployment
may tighten these values; it must not extend them beyond its signed enterprise
policy. Revocation is enforced locally as soon as a signed revocation update is
received. During a partition, the configured maximum propagation delay is the
bounded exposure window; it is not a claim of instantaneous global revocation.

The evaluator rejects missing/unverified snapshots, scope mismatch, expired grants,
clock rollback/future policy timestamps, stale revocation state, and expired
license grace. It never turns absence of a cached grant into an implicit grant.
License grace allows only READ and RESUMABLE_INTERNAL. External side effects,
administration and security-sensitive actions pause during grace and while the
control plane is unreachable. No offline path may bypass approval, tenant/resource
scope, audit, asset revocation or data isolation.

## Dependency failure behavior matrix

| Failure | Continue locally | Degrade | Pause | Deny |
|---|---|---|---|---|
| Cloud control plane unreachable | Existing sessions with fresh verified local identity/policy snapshots; authorized reads; resumable internal tasks; local audit | Use bounded policy cache; expose stale-state banner and last-sync time; queue idempotent sync/outbox events locally | New privileged approvals, policy mutation, membership/role changes, credential rotation, external irreversible actions | New identity enrollment, unknown subject/resource, expired/stale grant, revoked asset, absent local audit |
| Partner marketplace unreachable | Use previously installed, signature-verified and non-revoked cached assets | Catalog/search is marked stale; cached metadata only | New install, update, publish, review, rating, partner grant refresh | Unsigned/unverified package, revoked or tenant-incompatible asset |
| Model supplier unreachable | Continue with an already configured, approved local model route and cached model artifacts if available | Route to approved local fallback; mark model/provider and quality degradation; preserve run idempotency | If no approved local route exists, queue/pause the run for retry or human handoff | Unapproved provider/model, missing key, policy-incompatible route, or attempt to bypass provider allowlist |
| DNS / certificate service abnormal | Existing established sessions only while their authenticated channel remains valid; use unexpired cached DNS for explicitly allowlisted endpoints | Retry using bounded cached DNS; surface certificate/DNS health and expiry | New outbound connection when authoritative resolution or certificate validation is unavailable; wait for trusted resolver/PKI recovery | Expired, hostname-mismatched, revoked or untrusted certificate; DNS rebinding/out-of-allowlist target; never disable TLS verification |

“Continue” never means bypassing authentication, authorization, approval, encryption,
tenant isolation or audit. A DNS cache is not permission to ignore its TTL or
certificate validation. Existing TLS sessions must still terminate when their
peer identity/certificate becomes invalid or policy requires revalidation.

## Decision state machine

1. **ONLINE** — control plane reachable; synchronize signed identity/policy,
   revocation and license snapshots; persist local audit and sync watermark.
2. **OFFLINE_BOUNDED** — use only a verified cached authorization matching the
   exact subject/resource/action. Require policy snapshot freshness, grant expiry,
   revocation-delay bound, verified license and local audit availability.
3. **LICENSE_GRACE** — after license expiry, for at most the configured grace
   period, allow only READ and RESUMABLE_INTERNAL and return DEGRADED.
4. **PAUSED** — hold external side effects, administrative/security-sensitive
   work, or work requiring unavailable remote dependencies; preserve durable run
   state and idempotency keys for later retry/human handoff.
5. **DENIED** — reject absent/unverified/stale/mismatched/expired authorization,
   revoked assets, exhausted license grace, unsafe network identity, or failed
   local audit.
6. **RECONCILING** — on reconnect, apply signed revocations before replaying
   queued work; reconcile policy/license versions; deduplicate by existing task
   idempotency keys; append audit evidence; only then release paused work.

The policy primitive emits an audit event for each evaluated decision. The host
must make its audit writer durable and tamper-evident. A failed audit write is
fail-closed, not a warning-only condition.

## Recovery and operations checklist

- Keep local identity/policy/license snapshots encrypted at rest and versioned;
  keep signing and decryption keys in the existing secret/key-management adapter.
  Do not persist plaintext credentials in the offline policy cache.
- Synchronize revocations and policy versions before replaying any outbox or
  resuming paused Runs. Quarantine an asset immediately when a signed revocation
  arrives.
- Resume only idempotent tasks with durable execution checkpoints. External
  side effects require existing idempotency guarantees or human reconciliation;
  do not blindly replay.
- Record outage start/end, dependency, last successful sync, policy/license
  versions, action counts by decision, queued/paused task IDs, replay outcome,
  duplicate suppression and audit continuity.
- On control-plane recovery, verify clock health, certificate chain, DNS targets,
  signature chain, policy monotonicity and license state before leaving
  RECONCILING.

## Automated validation

Run the deterministic local tests:

```bash
python -m unittest tests.test_offline_policy -v
ruff check agent_platform/resilience/offline_policy.py agent_platform/resilience/__init__.py tests/test_offline_policy.py
```

These tests validate policy decisions, cache/revocation bounds, license grace,
scope isolation, audit fail-closed behavior and clock rollback. They are not a
network partition, real certificate/PKI, vendor outage, multi-node failover or
production disaster-recovery drill. The Issue's isolated-network and real backup/
restore RPO/RTO acceptance remains a separate field/production evidence gate;
this task does not claim it has been performed.
