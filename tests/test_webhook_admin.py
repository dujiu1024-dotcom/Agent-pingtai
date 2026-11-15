from __future__ import annotations

import unittest

from agent_platform.events.admin import WebhookSubscriptionService
from agent_platform.events.webhooks import WebhookSubscription


class FakeWebhookAdminStore:
    def __init__(self):
        self.items = {}
    async def create_subscription(self, subscription: WebhookSubscription, *, created_by: str):
        self.items[(subscription.tenant_id, subscription.subscription_id)] = {
            "subscription_id": subscription.subscription_id, "url": subscription.url,
            "topics": subscription.topics, "status": "active",
            "secret_ref": subscription.secret_ref, "created_by": created_by,
        }
    async def list_subscriptions(self, tenant_id: str):
        return [
            {k: v for k, v in item.items() if k != "secret_ref"}
            for (tenant, _), item in self.items.items() if tenant == tenant_id
        ]
    async def update_secret_ref(self, tenant_id: str, subscription_id: str, *, secret_ref: str):
        item = self.items.get((tenant_id, subscription_id))
        if item is None or item["status"] != "active":
            return None
        old = item["secret_ref"]
        item["secret_ref"] = secret_ref
        return old
    async def disable_subscription(self, tenant_id: str, subscription_id: str):
        item = self.items.get((tenant_id, subscription_id))
        if item is None or item["status"] != "active":
            return None
        item["status"] = "disabled"
        return item["secret_ref"]


class FakeSecrets:
    def __init__(self):
        self.values = {}
        self.revoked = []
        self.counter = 0
    async def store(self, *, tenant_id: str, secret: bytes):
        self.counter += 1
        ref = f"secret://{tenant_id}/{self.counter}"
        self.values[ref] = secret
        return ref
    async def revoke(self, *, secret_ref: str):
        self.revoked.append(secret_ref)
        self.values.pop(secret_ref, None)


class WebhookAdminTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_list_rotate_disable_are_tenant_scoped_and_hide_secret_refs(self):
        store, secrets = FakeWebhookAdminStore(), FakeSecrets()
        service = WebhookSubscriptionService(store=store, secrets=secrets)
        subscription_id = await service.create(
            tenant_id="tenant-a", actor_id="user-a",
            url="https://hooks.example.test/agent", topics=("run.completed",),
            signing_secret=b"a" * 32,
        )
        listed = await service.list(tenant_id="tenant-a")
        self.assertEqual(len(listed), 1)
        self.assertNotIn("secret_ref", listed[0])
        self.assertEqual(await service.list(tenant_id="tenant-b"), [])
        self.assertTrue(await service.rotate(
            tenant_id="tenant-a", subscription_id=subscription_id,
            signing_secret=b"b" * 32,
        ))
        self.assertEqual(secrets.revoked, ["secret://tenant-a/1"])
        self.assertTrue(await service.disable(
            tenant_id="tenant-a", subscription_id=subscription_id,
        ))
        self.assertEqual(secrets.revoked, ["secret://tenant-a/1", "secret://tenant-a/2"])
        self.assertFalse(await service.disable(
            tenant_id="tenant-b", subscription_id=subscription_id,
        ))

    async def test_create_rejects_weak_secret_and_private_destination(self):
        service = WebhookSubscriptionService(store=FakeWebhookAdminStore(), secrets=FakeSecrets())
        with self.assertRaises(ValueError):
            await service.create(
                tenant_id="tenant-a", actor_id="user-a",
                url="https://hooks.example.test", topics=("run.completed",),
                signing_secret=b"weak",
            )
        with self.assertRaises(ValueError):
            await service.create(
                tenant_id="tenant-a", actor_id="user-a",
                url="https://127.0.0.1/hook", topics=("run.completed",),
                signing_secret=b"a" * 32,
            )


if __name__ == "__main__":
    unittest.main()
