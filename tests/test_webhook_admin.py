from __future__ import annotations

import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.events.admin import WebhookSubscriptionService
from agent_platform.events.webhooks import WebhookSubscription
from agent_platform.api.webhooks import create_webhook_admin_router
from agent_platform.identity.models import Principal


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


class FakeAudit:
    def __init__(self):
        self.events = []
    async def record(self, **event):
        self.events.append(event)


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


    async def test_mutations_fail_closed_without_audit_writer(self):
        app = FastAPI()
        store, secrets = FakeWebhookAdminStore(), FakeSecrets()
        service = WebhookSubscriptionService(store=store, secrets=secrets)
        app.include_router(create_webhook_admin_router(
            service=service, authorize_manage=lambda actor: True,
        ))
        @app.middleware("http")
        async def attach_principal(request, call_next):
            setattr(request.state, "identity.principal", Principal(
                user_id="user-a", tenant_id="tenant-a", session_id="s",
                email="a@example.test", display_name="A",
            ))
            return await call_next(request)
        response = TestClient(app).post("/api/platform/v1/webhooks", json={
            "url": "https://hooks.example.test/agent",
            "topics": ["run.completed"], "signing_secret": "s" * 32,
        })
        self.assertEqual(response.status_code, 503)
        self.assertEqual(store.items, {})
        self.assertEqual(secrets.values, {})

    async def test_http_api_derives_tenant_and_actor_from_authenticated_principal(self):
        store, secrets = FakeWebhookAdminStore(), FakeSecrets()
        service = WebhookSubscriptionService(store=store, secrets=secrets)
        audit = FakeAudit()

        def client_for(user_id):
            app = FastAPI()
            app.include_router(create_webhook_admin_router(
                service=service, authorize_manage=lambda actor: actor.user_id == "user-a",
                audit_writer=audit,
            ))

            @app.middleware("http")
            async def attach_principal(request, call_next):
                setattr(request.state, "identity.principal", Principal(
                    user_id=user_id, tenant_id="tenant-a", session_id="session-1",
                    email="user@example.test", display_name=user_id,
                ))
                return await call_next(request)

            return TestClient(app)

        with client_for("user-a") as client:
            created = client.post("/api/platform/v1/webhooks", json={
                "url": "https://hooks.example.test/agent",
                "topics": ["run.completed"],
                "signing_secret": "s" * 32,
                "tenant_id": "tenant-attacker",
                "actor_id": "attacker",
            })
            self.assertEqual(created.status_code, 201)
            subscription_id = created.json()["subscription_id"]
            listed = client.get("/api/platform/v1/webhooks")
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(len(listed.json()["items"]), 1)
            self.assertNotIn("secret_ref", listed.json()["items"][0])
            rotated = client.post(
                f"/api/platform/v1/webhooks/{subscription_id}/rotate",
                json={"signing_secret": "r" * 32},
            )
            self.assertEqual(rotated.status_code, 200)
            self.assertEqual(
                [event["action"] for event in audit.events],
                ["webhook.subscription.create.requested", "webhook.subscription.secret_rotate.requested"],
            )

        with client_for("user-b") as denied:
            self.assertEqual(
                denied.get("/api/platform/v1/webhooks").status_code, 403,
            )


if __name__ == "__main__":
    unittest.main()
