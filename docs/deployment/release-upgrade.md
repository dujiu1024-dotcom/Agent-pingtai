# Release upgrade manifest and execution contract

The operations handbook uses scripts/release_upgrade.py to validate an immutable
release manifest and emit an ordered upgrade and rollback plan. The example at
deploy/release-upgrade-manifest.example.json matches the current validator schema;
replace every example value with reviewed release evidence before use.

## Required manifest fields

- release_id, from_version, to_version: unique release identifier and increasing
  semantic versions.
- image_digest: immutable image reference pinned by sha256 digest.
- config_revision and previous_config_revision: immutable configuration revisions.
- compatibility: app/schema compatibility range, positive compatibility window,
  and explicit old/new coexistence.
- migration: successful preflight, verified backup reference, expand/contract
  declaration, destructive-contract timing and an allowed rollback strategy.
- rollout: integer batch percentages totaling 100, health gate enabled and pause
  duration.
- rollback: previous immutable image digest, previous config revision, config
  snapshot reference and automatic stop/rollback policy on health failure.

The planner blocks invalid or incomplete manifests. It emits steps only; it does
not run migrations, deploy containers, change traffic or execute rollback. An
approved deployment executor must record each step's result and stop immediately
when a health or compatibility gate fails.

## Execution sequence

1. Resolve release commit and immutable image digest; verify provenance/signature
   using the organization's image policy.
2. Run backup integrity verification and confirm restore evidence is current.
3. Check schema drift, lock risk, disk capacity, replication lag, pending jobs and
   old/new application compatibility.
4. Apply additive expand migration once through the controlled migration job.
5. Roll out batches in order, pausing and evaluating readiness, error rate, latency,
   queue/outbox lag and dependency health after each batch.
6. Commit the release only after all batches and smoke tests pass.
7. Delay destructive contract migration until the compatibility window closes and
   the rollback horizon has expired.
8. On failure, stop rollout, drain new-version traffic, restore the previous image
   and config, then apply the approved compensating migration or follow the verified
   backup restore runbook. Never assume a schema downgrade is safe.

## Validation

```sh
python scripts/release_upgrade.py --manifest deploy/release-upgrade-manifest.example.json
```

The example includes an illustrative backup reference and image digests. Its
successful validation is a schema/planning check only; it is not evidence that
those images, backups or migrations exist in a deployment environment.
