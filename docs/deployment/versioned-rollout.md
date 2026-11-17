# Versioned deployment and rollout policy

The deployment domain primitives live in `agent_platform.deployment`. They are
provider-neutral: a deployment adapter is responsible for applying traffic
weights, running probes, and restoring the previous artifact.

## Strategies

- **Gray release**: default traffic steps 1%, 5%, 10%, 25%, 50%, 100%.
- **Canary**: default traffic steps 5%, 25%, 50%, 100%; evaluate health between steps.
- **Blue/green**: one 100% traffic switch after the new version is ready.
- **Rolling**: default progressive steps 10%, 25%, 50%, 75%, 100%.

Every deployment records tenant, service, immutable version identifiers, stage
history, and rollback reason. Health gates require a minimum request sample and
enforce error-rate and p95-latency ceilings. A failed gate automatically rolls
back to the previous version by default; disabling auto rollback pauses the
deployment for an operator decision.

## Maintenance windows

A maintenance window uses ISO weekdays (Monday=0), local wall-clock start/end,
and an IANA timezone. The end is exclusive; overnight windows are supported.
Starting outside the window is rejected. The caller must pass a timezone-aware
instant; naive timestamps are rejected.

## Adapter contract

The state machine decides whether to advance, pause, or roll back. Production
adapters must make traffic changes idempotent, persist deployment state, use
tenant-scoped authorization, emit audit events, and make rollback restore the
previous version before reporting success. This module does not directly call
a cloud provider or mutate live traffic.
