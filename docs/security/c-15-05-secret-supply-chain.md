# C-15-05 — Secret and supply-chain governance

## Implemented code paths

- `agent_platform/security/secrets.py`: tenant-scoped secret references, version metadata, create/rotate/revoke, fail-closed resolution, and retryable backend revocation.
- `agent_platform/security/vault_kv_v2.py`: Vault KV v2 immutable version paths, TLS-only origin validation, bounded timeouts, namespace support, and sanitized failures.
- `agent_platform/security/vault_auth.py`: AppRole login and in-memory token renewal; no token persistence or response-body logging. Inject as `token_provider=provider` into the Vault backend.
- `agent_platform/security/kms_envelope.py`: AWS KMS GenerateDataKey/Decrypt envelope encryption with AES-256-GCM, authenticated tenant/ref/version context, and PostgreSQL ciphertext-only persistence.
- `agent_platform/storage/postgres/065_secret_ciphertexts.sql`: ciphertext-only storage with a foreign key to secret version metadata.
- `agent_platform/observability/redaction.py` and `agent_platform/security/audit.py`: recursive secret-key and token-pattern redaction for audit payloads.

## Vault deployment policy (least privilege)

Use a dedicated AppRole per workload, with short TTL, renewable service token, response wrapping for SecretID delivery, and no root token in application configuration. Scope the policy to only the workload's tenant prefix and required KV v2 verbs:

```hcl
path "platform-secrets/data/<tenant>/*" {
  capabilities = ["create", "read", "update"]
}
path "platform-secrets/metadata/<tenant>/*" {
  capabilities = ["read", "delete"]
}
path "auth/token/renew-self" {
  capabilities = ["update"]
}
```

Do not grant `sys/*`, mount administration, policy administration, or broad list access. Separate rotation and revocation operator identities from runtime identities.

## AWS KMS deployment policy (least privilege)

Create a customer-managed symmetric encryption key. The runtime role needs only `kms:GenerateDataKey` and `kms:Decrypt` on that key. Restrict both actions with encryption-context conditions for `purpose=agent-platform-secret`; optionally constrain tenant/ref prefixes through separate roles or key policies. Deny key administration to the runtime role. Supply the regional KMS client and key ARN through the deployment composition root; do not place key material in environment variables.

## Rotation, revocation, and recovery

SecretManager writes a pending metadata version before backend storage, activates the new version atomically, and schedules old-version cleanup. Failed cleanup remains visible as pending revocation and is retried by `retry_revocations`; missing active material never falls back to an older version. KMS revocation deletes the ciphertext envelope; Vault revocation deletes the version-specific KV metadata path. KMS key disable/deletion is an operator recovery action and must not be automated by the runtime identity.

## Dependency and logging controls

- `requirements-platform.txt` declares the cryptographic primitive dependency.
- CI runs `pip-audit` against platform and optional runtime requirement sets.
- Do not log Vault tokens, AppRole SecretIDs, plaintext secrets, KMS plaintext data keys, request bodies, or provider response bodies. Error messages contain status/category only.
- Pin production images by digest and use lock/constraints files in deployment builds. The repository's requirement ranges remain compatibility declarations, not a production lockfile.

## Verification boundary

Unit tests cover Vault AppRole login/renewal, Vault failure redaction, KMS context binding, envelope tamper rejection, metadata cleanup, and fake provider failures. Real Vault/KMS IAM policy and live key rotation are intentionally not exercised in this task; they remain deployment-site acceptance evidence.
