# Trusted publisher registry and production lifecycle state

The package's `publisher_key_id` is an untrusted lookup hint. Public-key material must come from the platform-controlled `platform_trusted_publisher_keys` registry; an extension package must never provide its own trust anchor.

## Key lifecycle

- Register a tenant- and extension-scoped publisher key with a unique key ID.
- Rotate by registering the replacement key and revoking prior key IDs in one PostgreSQL transaction.
- Revoke a key explicitly; the resolver returns the revoked state and the runtime rejects it.
- The runtime resolves the key at installation and again before every invocation, so expiry and revocation take effect for already-installed artifacts.
- API writes require an extension-management authorization decision and a successful audit intent before changing registry state. Audit details contain identifiers only, never key material.

Public keys are stored as public PEM bytes. Private keys must remain in the publisher's signing system and must never be uploaded to this API.

## Durable extension state

`PostgresExtensionStateStore` is tenant-bound and persists the validated manifest, enabled/disabled state and immutable artifact reference. It intentionally rejects arbitrary Python objects: production state must reference a verified artifact in a tenant-isolated artifact store.

`InMemoryExtensionStateStore` remains for tests and development only. Production must inject the PostgreSQL state store, trusted publisher registry, verified package resolver, tenant-isolated artifact store, OCI sandbox runner, permission authorizer and append-only audit writer. Missing trust resolution or permission authorization must fail closed.

## API

- `POST /api/platform/v1/extensions/publishers/keys`: register a key.
- Include `revoke_key_ids` to atomically rotate to a new key and revoke previous key IDs.
- `POST /api/platform/v1/extensions/publishers/{extension_id}/keys/{key_id}/revoke`: revoke a key.

The authenticated principal supplies tenant and actor identity; request fields cannot select another tenant or actor.
