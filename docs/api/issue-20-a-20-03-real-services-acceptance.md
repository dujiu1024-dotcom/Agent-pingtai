# A-20-03 real-service webhook runtime acceptance

This test closes part of the evidence gap recorded in #267 for Issue #20 / A-20-03.
It runs inside the PostgreSQL/Redis integration job with a real HashiCorp Vault
dev service; it does not substitute mocks for PostgreSQL, Redis, Vault KV v2,
secret metadata persistence, or the identity audit ledger.

## Automated coverage

- Applies the repository PostgreSQL migrations and creates an isolated tenant.
- Connects to the real Redis service and verifies availability with PING.
- Enables a real Vault KV v2 mount, writes a signing secret through
  `VaultKvV2SecretBackend`, persists version metadata through
  `PostgresSecretMetadataRepository`, and resolves the active secret through
  `SecretManager`.
- Creates and enqueues a webhook through `PostgresWebhookStore`; repeats enqueue
  and asserts the database uniqueness constraint prevents duplicate delivery.
- Starts and stops the real `WebhookDeliveryWorker` around a
  `WebhookDispatcher` wired to the PostgreSQL store, Vault-backed secret resolver,
  and PostgreSQL append-only audit writer.
- Checks the signed callback payload, final durable delivery state, and persisted
  write-ahead/result audit events; verifies the secret is not present in audit data.

## Deliberate evidence boundary

The callback transport in this test is a capture probe, not a real remote HTTPS
receiver. The test still exercises the production dispatcher, signature generation,
secret resolution, egress address policy contract, PostgreSQL delivery lease/state,
worker lifecycle, and audit persistence. It does not claim to prove TLS certificate
validation, DNS pinning against a live public DNS answer, outbound socket delivery,
or an actual provider's callback acknowledgement.

The earlier A-20-03 field blocker therefore remains for a controlled deployment
exercise with a real Secret Manager/Vault identity, runtime Redis/PostgreSQL,
public HTTPS callback receiver, and audit read access. The CI Vault dev root token
is ephemeral and is not a production credential.

## Run

```bash
AGENT_PLATFORM_TEST_DSN=... \
AGENT_PLATFORM_TEST_REDIS_URL=... \
AGENT_PLATFORM_TEST_VAULT_ADDR=... \
AGENT_PLATFORM_TEST_VAULT_TOKEN=... \
python -m unittest tests.test_issue_20_a_20_03_real_services_acceptance -v
```

The Platform Integration workflow supplies disposable PostgreSQL, Redis, and Vault
services and invokes this module as part of its integration suite.
