# Credential Store and Agent/Workflow Grants

C-27-01 adds a tenant-scoped credential catalog over the existing platform
`SecretManager`. Secret material remains in Vault KV v2 or KMS envelope storage;
PostgreSQL stores only opaque refs, labels, lifecycle metadata and grants.

## Management API

The optional router is enabled only when the host supplies both
`credential_service_resolver(principal)` and `credential_authorizer(principal, resource_type, resource_id, action)`.

- `POST /api/platform/v1/credentials`: create an encrypted credential. The request
  accepts `credential_id`, `provider`, `display_name`, `value`, and `scopes`.
  The response never contains `value` or `secret_ref`.
- `GET /api/platform/v1/credentials?limit=100`: list metadata for the authenticated tenant.
- `GET /api/platform/v1/credentials/{credential_id}`: read metadata.
- `POST /api/platform/v1/credentials/{credential_id}/grants`: grant a subset of
  declared labels to an Agent or Workflow after authorizing both the credential
  and target resource.
- `GET /api/platform/v1/credentials/{credential_id}/grants`: list grants.
- `DELETE /api/platform/v1/credentials/{credential_id}`: revoke all grants and
  all SecretManager versions.

## Runtime contract

Trusted runtime composition resolves credentials through
`CredentialService.resolve_for_runtime(tenant_id, credential_id, grantee_type,
grantee_id, required_scopes)`. It checks tenant, active credential, active
Agent/Workflow grant and requested-scope subset before resolving plaintext.
Resolution is write-ahead audited and is not exposed as an HTTP endpoint.

## Security invariants

- Tenant and actor identities come from the authenticated Principal, never request JSON.
- Agent/Workflow grants are explicit, tenant-bound, and cannot exceed credential labels.
- Secret plaintext is accepted only on create and is not returned, logged, audited or stored in metadata.
- Audit failure blocks create/grant/revoke/resolve before the protected side effect.
- Credential revocation disables all grants before deleting SecretManager versions.
- Host must inject a tenant-aware resolver and authorize both credential management and
  target Agent/Workflow access. Vault/KMS credentials and key policy remain host-managed.
- This task does not implement Connector Catalog or BaseConnector migration (C-27-02/03).
