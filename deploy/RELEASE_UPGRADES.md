# Release upgrade procedure

This procedure is separate from infrastructure bootstrap. It upgrades only the
explicitly configured Compose services; it does not imply that the repository
has a complete production API/worker topology.

## Configuration

Set these values in the protected release env file (mode 0600):

- CURRENT_RELEASE_VERSION: currently running application release (SemVer).
- RELEASE_VERSION: target release (SemVer).
- RELEASE_COMPATIBILITY_MIN / RELEASE_COMPATIBILITY_MAX: inclusive range of
  currently running releases supported during the compatibility window.
- UPGRADE_SERVICES: comma-separated Compose services, upgraded one at a time.
- PRE_UPGRADE_BACKUP_COMMAND: mandatory operator-approved backup command.
- PG_DSN: when set, enables read-only migration ledger/checksum preflight.
- MIGRATION_COMMAND: optional forward migration command, run after backup and
  before service rollout.

Use expand/migrate/contract schema changes. During the compatibility window,
deploy code that tolerates both old and expanded schema; remove old schema only
in a later release after all old instances are drained.

## Preflight and rollout

From repository root:

    scripts/release_upgrade.sh --check
    scripts/release_upgrade.sh --apply

Preflight validates Compose, versions, the inclusive compatibility window,
the ordered service list and (when PG_DSN is set) migration filenames and
checksums against the applied migration ledger. It does not change services or
database state.

Apply requires a successful backup command. It snapshots the release env,
runs the optional migration command, then upgrades each listed service
sequentially with --no-deps --wait. A failed service restores the prior env
snapshot and rolls the listed services back.

## Rollback and limits

    scripts/release_upgrade.sh --rollback

Rollback restores the latest successful configuration snapshot and restarts
the configured services in order. It does not reverse database migrations.
Only migrations explicitly designed as reversible should be reversed through a
separately reviewed migration procedure. If a migration is not backward
compatible, automatic application rollback must not be claimed safe.

Sequential Compose recreation is a controlled rollout, not orchestrator-managed
multi-replica zero-downtime rolling deployment. Validate service readiness and
compatibility in the target environment before production use. Real-server
validation is a separate operational acceptance step.
