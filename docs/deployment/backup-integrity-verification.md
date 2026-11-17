# Backup integrity verification

The backup policy and capture schedules are defined in [BACKUP_POLICY.md](../deploy/BACKUP_POLICY.md) and [backup-policy.json](../deploy/backup-policy.json). This verifier closes the operational gap between creating sidecars and periodically proving that each artifact still matches its metadata.

## Routine integrity sweep

Run after a backup batch and as a scheduled daily audit:

```bash
python scripts/verify_platform_backups.py --backup-dir /srv/agent-platform/backups
```

The command discovers PostgreSQL, Redis, MinIO object and configuration artifacts. It requires each artifact to be a regular file with a matching SHA-256 sidecar and JSON manifest. The sidecar filename, manifest format, artifact filename and manifest digest are all bound to the actual artifact. Missing, malformed, symlinked or mismatched metadata fails closed.

## Decryption and format validation

Run in a restricted recovery-validation environment where the age identity and native tools are available:

```bash
AGE_IDENTITY=/secure/backup-age-identity python scripts/verify_platform_backups.py --backup-dir /srv/agent-platform/backups --decrypt
```

This authenticates each age payload and validates PostgreSQL custom dumps with `pg_restore --list`, Redis RDB files with `redis-check-rdb`, and object/configuration tar archives with path traversal, link and duplicate-entry checks. The identity is supplied out of band and is never copied into the backup directory. Plaintext is held in a private temporary directory and removed on exit.

## Exit codes and limits

- `0`: all discovered artifacts passed the selected verification level.
- `1`: at least one artifact failed, or no recognized artifacts were found.
- `2`: invocation is unsafe or incomplete (for example, invalid backup directory or missing decryption identity).

A checksum sweep proves artifact consistency, not recoverability. Decryption/format validation is stronger but still does not prove database restore semantics, object references, cross-component consistency, or measured RPO/RTO. Those require a controlled restore drill. Do not run restore commands against production as part of this audit.
