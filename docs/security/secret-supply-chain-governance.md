# Secret and supply-chain governance

## Runtime wiring

- Prefer `VaultAppRoleTokenProvider` + `VaultKvV2SecretBackend` for Vault deployments. Supply AppRole role/secret IDs from the deployment secret injector, never from source control or request payloads.
- Prefer `KmsEnvelopeSecretBackend` + `AwsKmsDataKeyProvider` + `PostgresSecretCiphertextStore` where KMS envelope encryption is required. Plaintext and data keys must not be persisted to SQL.
- Compose either backend with `SecretManager` and `PostgresSecretMetadataRepository`. The application receives a secret only at the point of use; API responses and audit events expose references and versions, not values.
- Keep Vault address, namespace, mount, KMS key ARN and backend selection in trusted deployment configuration. Do not permit tenant input to select arbitrary vault paths or KMS keys.

## Vault least-privilege policy

Bind a dedicated AppRole to a policy scoped to the tenant path prefix actually owned by the workload. Example (replace mount and prefix per deployment):

```hcl
path "platform-secrets/data/tenant-a/*" {
  capabilities = ["create", "update", "read"]
}
path "platform-secrets/metadata/tenant-a/*" {
  capabilities = ["read", "delete"]
}
path "auth/token/lookup-self" {
  capabilities = ["read"]
}
path "auth/token/renew-self" {
  capabilities = ["update"]
}
```

Do not grant `sudo`, `root`, `sys/mounts/*`, broad `secret/*`, or cross-tenant access. Separate provisioning, runtime read/write, and emergency revocation identities. Use short-lived renewable service tokens and rotate the AppRole secret ID through the deployment secret manager.

## KMS least privilege

Grant the runtime identity only `kms:GenerateDataKey` and `kms:Decrypt` on the one configured key ARN. Constrain encryption context with `purpose=agent-platform-secret`; use key-policy/IAM conditions where supported. Deny wildcard resources and key administration to the runtime identity. Key rotation is performed by the key-management role, not the application role.

## Rotation, revocation and recovery

1. Create a new immutable secret version and persist only version metadata.
2. Write the new material to Vault/KMS backend, then atomically activate the new metadata version.
3. Mark prior versions revoked before backend cleanup; never fall back to revoked or unavailable versions.
4. Failed backend deletion remains pending and is retried with `SecretManager.retry_revocations`. Alert on pending age/count and do not report revocation complete until backend cleanup is confirmed.
5. For compromised credentials, revoke all metadata versions first, then retry backend cleanup. Rotate the upstream credential and deploy a fresh version before restoring service.
6. KMS key disablement/deletion is an operator-controlled incident action; preserve encrypted backups and recovery approvals separately.

## Log redaction

Install `configure_secret_log_redaction` during process bootstrap on the root logger (or every application logger that owns handlers). Register known credential values at the boundary where they enter process memory and unregister them after their lifecycle ends. Redaction is defense in depth: never intentionally log secret values, request authorization headers, Vault responses, or KMS plaintext.

## Dependency supply chain

The `dependency-security` workflow runs `pip-audit` against platform dependency manifests on pull requests, main pushes, and a weekly schedule. Remediate exploitable findings by upgrading to a compatible patched release; document unavoidable exceptions with owner and expiry. Pin deployment artifacts with hashes/lockfiles in release packaging and review transitive dependency changes.
