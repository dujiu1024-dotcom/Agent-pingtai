# Multi-expert Blackboard collaboration contract (C-13-01)

## Expert registration and capability descriptions

The workflow consumes an `ExpertRegistry` contract. Production composition uses `ControlPlaneExpertRegistry`, which resolves only the active published Agent version in the configured tenant. The registry exposes a stable expert ID, display name, description, enabled state and a set of capability identifiers. Runtime objects, credentials and provider secrets are not sourced from profile metadata.

Planner assignments identify an `expert_id` and may request a bounded set of `required_capabilities`. When a registry is configured, planning rejects missing/disabled experts and capability mismatches, and attaches canonical profile metadata to the assignment. Execution resolves the profile again immediately before invoking the expert so revocation or capability changes between planning and dispatch fail closed.

## Task dispatch and parallel execution

The LangGraph workflow fans out one `run_expert` branch per validated assignment using Send/map-reduce semantics. A bounded fan-out limit (default 8, hard configuration ceiling 32) and duplicate expert ID rejection prevent accidental unbounded or duplicate dispatch. Empty assignment sets proceed directly to synthesis.

Every branch receives the same tenant, task, run and objective identity, plus an isolated deep copy of the shared run context and assignment. Branch-local mutation therefore cannot affect sibling experts or the caller's context. The `contributions` state channel uses an additive reducer so independently completed branches are collected for synthesis.

## Shared context and result aggregation

The common context carries workflow-scoped metadata; tenant/task/run identity is authoritative and overwrites conflicting caller context values. Each expert receives its own copy. Expert contributions are persisted as Blackboard claims with deterministic IDs, author identity and source references. The synthesizer receives the complete collected contribution set and its output is persisted as an artifact referencing the contributing claims. This preserves lineage from aggregate result back to expert work.

## Failure semantics and boundaries

An unregistered, disabled or insufficiently capable expert is rejected before its callback runs. Registry changes after planning are rechecked at execution. Fan-out validation fails the plan as a whole rather than silently dropping invalid assignments. Parallel orchestration is provided by LangGraph; durable Blackboard writes are idempotent. This contract does not claim provider-backed execution or multi-instance production validation; those require their own integration evidence.

## Deterministic parallel aggregation and conflict review

Parallel expert branches are collected by the additive reducer, whose completion order is not stable. Before synthesis and review, the workflow creates an isolated deep-copied view sorted by canonical expert ID and then Blackboard entry ID. This makes aggregation reproducible across different branch completion schedules and prevents downstream callbacks from mutating the collected state. Ordering is not a conflict-resolution policy: contradictory claims remain explicit review conflicts and must pass the existing one-decision-per-conflict validation, with durable decision records linked to the conflict and its evidence.


## Evidence validation and immutable conflict decisions

Conflict resolution is fail-closed for Blackboard-owned evidence: every conflict must point to a persisted CONFLICT entry and its persisted CHALLENGE entry in the active workflow scope. UUID-shaped Blackboard evidence references supplied by a decision must resolve to existing entries in that same scope; opaque connector/document references remain delegated to their owning evidence service. Each decision records both the conflict and challenge IDs plus its evidence references. Idempotent writes compare decision status and immutable metadata as well as semantic payload, so a retry cannot rewrite an accepted decision as rejected or otherwise mutate its audit record.


## At-least-once delivery and external-effect idempotency (A-13-03)

Redis Streams delivery and worker execution remain **at least once**; neither a stream message ID nor a consumer receipt alone can guarantee exactly-once external effects. Every handler that can cause an external effect must use `IdempotentEventHandler` (or an equivalent adapter) and pass its stable key to the downstream system:

`tenant:<tenant_id>:event:<canonical UUID event_id>`

The key is deliberately independent of Redis message ID, consumer name, worker instance, and retry attempt. The downstream adapter must durably bind that key to a request fingerprint and its result, atomically with the effect where the provider supports it. A repeated key with a different request must be rejected, not silently treated as a replay. If a remote provider offers native idempotency keys, forward this key; otherwise use a provider-specific durable operation ledger/outbox and reconcile ambiguous outcomes before retrying. A caller-side receipt cannot close the crash window between a successful external effect and receipt completion.

The acceptance test `tests/test_issue_13_a_13_03_at_least_once_effects.py` models the critical ambiguous-response window: the downstream commits once, the first worker loses the response, and a restarted worker receives the same event under a different stream message ID. It verifies one effect, stable tenant/event identity, and rejection of payload mutation under a reused key. This is a deterministic contract test, not evidence of a live provider or multi-instance Redis deployment.

**Hybrid Blackboard decision is unchanged:** PostgreSQL remains the authoritative, versioned Blackboard/event/outbox store; Redis Streams remains the delivery/projection transport. Decision and evidence records continue through the existing Blackboard repository and immutable decision history. The idempotency key protects downstream effects; it does not replace Blackboard persistence, alter the Hybrid topology, or claim exactly-once message delivery.
