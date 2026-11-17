"""A-20-03 real PostgreSQL, Redis and Vault runtime integration.

The test uses real service containers for durable delivery state, secret storage,
and Redis availability. The callback transport is an in-process contract probe:
production pinned HTTPS egress still requires a separately provisioned public
callback receiver and is deliberately not bypassed by this test.
"""
from __future__ import annotations

import asyncio
import json
import os
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4

DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")
REDIS_URL = os.environ.get("AGENT_PLATFORM_TEST_REDIS_URL")
VAULT_ADDR = os.environ.get("AGENT_PLATFORM_TEST_VAULT_ADDR")
VAULT_TOKEN = os.environ.get("AGENT_PLATFORM_TEST_VAULT_TOKEN")

if DSN and REDIS_URL and VAULT_ADDR and VAULT_TOKEN:
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row
    from redis.asyncio import Redis

    from agent_platform.events.webhook_worker import WebhookDeliveryWorker
    from agent_platform.events.webhooks import (
        PostgresWebhookStore,
        WebhookDispatcher,
        WebhookEgressPolicy,
        WebhookPayload,
        WebhookSubscription,
        verify_signature,
    )
    from agent_platform.security.audit import (
        PostgresAuditWriter,
        PostgresIdentityAuditReader,
    )
    from agent_platform.security.secrets import (
        PostgresSecretMetadataRepository,
        SecretManager,
    )
    from agent_platform.security.vault_kv_v2 import VaultKvV2SecretBackend


class _CallbackProbe:
    """Observe the fully signed dispatcher request without weakening egress policy."""

    def __init__(self):
        self.requests = []
        self.delivered = asyncio.Event()

    async def post_pinned(
        self, url, *, resolved_addresses, body, headers, timeout_seconds,
    ):
        self.requests.append({
            "url": url,
            "resolved_addresses": resolved_addresses,
            "body": body,
            "headers": dict(headers),
            "timeout_seconds": timeout_seconds,
        })
        self.delivered.set()
        return 202


@unittest.skipUnless(
    DSN and REDIS_URL and VAULT_ADDR and VAULT_TOKEN,
    "PostgreSQL, Redis and Vault integration services are required",
)
class A2003RealServicesAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenant_id = f"a20-03-{uuid4().hex}"
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            for migration_path in sorted(
                Path("agent_platform/storage/postgres").glob("*.sql")
            ):
                await conn.execute(migration_path.read_text(), prepare=False)
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id, display_name) VALUES (%s, %s)",
                (self.tenant_id, "A-20-03 real services acceptance"),
            )

        async def connect():
            return await AsyncConnection.connect(DSN, row_factory=dict_row)

        self.connect = connect
        self.redis = Redis.from_url(REDIS_URL, decode_responses=True)
        await self.redis.ping()

        self.vault = VaultKvV2SecretBackend(
            address=VAULT_ADDR,
            token=VAULT_TOKEN,
            mount="platform-a20-03",
            allow_http=VAULT_ADDR.startswith("http://"),
        )
        self._enable_vault_kv_v2()

        self.secret_manager = SecretManager(
            backend=self.vault,
            metadata=PostgresSecretMetadataRepository(connect),
        )
        self.store = PostgresWebhookStore(connect)
        self.audit_writer = PostgresAuditWriter(connect)
        self.audit_reader = PostgresIdentityAuditReader(connect)

    def _enable_vault_kv_v2(self):
        request = Request(
            f"{VAULT_ADDR.rstrip('/')}/v1/sys/mounts/platform-a20-03",
            data=json.dumps({
                "type": "kv",
                "options": {"version": "2"},
            }).encode(),
            headers={
                "X-Vault-Token": VAULT_TOKEN,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=5) as response:
                self.assertIn(response.status, (200, 204))
        except Exception as exc:
            # A fresh Actions Vault service should not have this mount. Treat
            # mount setup errors as failures, not as a silent fake-backend fallback.
            raise AssertionError("could not enable real Vault KV v2 test mount") from exc

    async def asyncTearDown(self):
        await self.redis.aclose()

    async def test_real_postgres_vault_redis_worker_delivery_and_audit(self):
        secret_ref = f"secret://{self.tenant_id}/webhooks/integration"
        signing_secret = "a20-03-integration-signing-secret-0123456789"
        await self.secret_manager.create(
            secret_ref=secret_ref,
            value=signing_secret,
        )
        self.assertEqual(
            await self.secret_manager.resolve(secret_ref=secret_ref),
            signing_secret,
        )

        subscription = WebhookSubscription(
            subscription_id=str(uuid4()),
            tenant_id=self.tenant_id,
            url="https://hooks.example.test/platform-events",
            secret_ref=secret_ref,
            topics=("run.completed",),
        )
        await self.store.create_subscription(
            subscription,
            created_by="a20-03-integration-test",
        )
        payload = WebhookPayload(
            tenant_id=self.tenant_id,
            event_id=str(uuid4()),
            topic="run.completed",
            body={"run_id": "run-a20-03", "status": "completed"},
        )
        self.assertEqual(await self.store.enqueue(payload), 1)
        # Unique (tenant, subscription, event) constraint makes enqueue idempotent.
        self.assertEqual(await self.store.enqueue(payload), 0)

        callback = _CallbackProbe()

        async def resolve_signing_secret(ref):
            value = await self.secret_manager.resolve(secret_ref=ref)
            return value.encode("utf-8")

        dispatcher = WebhookDispatcher(
            store=self.store,
            transport=callback,
            resolve_secret=resolve_signing_secret,
            egress_policy=WebhookEgressPolicy(
                resolver=lambda _host, _port: ("93.184.216.34",),
            ),
            audit_sink=self.audit_writer,
            max_attempts=3,
            timeout_seconds=3,
        )
        worker = WebhookDeliveryWorker(
            dispatcher=dispatcher,
            interval_seconds=0.1,
            batch_limit=5,
        )
        await worker.start()
        try:
            await asyncio.wait_for(callback.delivered.wait(), timeout=5)
        finally:
            await worker.stop()

        self.assertEqual(len(callback.requests), 1)
        delivered = callback.requests[0]
        self.assertEqual(delivered["resolved_addresses"], ("93.184.216.34",))
        self.assertEqual(delivered["headers"]["Idempotency-Key"], payload.event_id)
        self.assertTrue(verify_signature(
            signing_secret.encode("utf-8"),
            delivered["headers"]["X-Platform-Timestamp"],
            delivered["body"],
            delivered["headers"]["X-Platform-Signature"],
        ))
        self.assertEqual(json.loads(delivered["body"])["id"], payload.event_id)

        async with await self.connect() as conn:
            row = await (await conn.execute(
                """SELECT status, attempts, response_code
                   FROM platform_webhook_deliveries
                   WHERE tenant_id=%s AND event_id=%s""",
                (self.tenant_id, payload.event_id),
            )).fetchone()
        self.assertEqual(row["status"], "delivered")
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["response_code"], 202)

        audit = await self.audit_reader.list_events(
            tenant_id=self.tenant_id,
            actor_id=WebhookDispatcher.SYSTEM_ACTOR_ID,
            limit=10,
        )
        actions = [event["action"] for event in audit]
        self.assertIn("webhook.delivery.attempt", actions)
        self.assertIn("webhook.delivery", actions)
        self.assertTrue(all(event["tenant_id"] == self.tenant_id for event in audit))
        self.assertNotIn(signing_secret, json.dumps(audit))


if __name__ == "__main__":
    unittest.main()
