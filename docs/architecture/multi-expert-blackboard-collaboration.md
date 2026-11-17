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
