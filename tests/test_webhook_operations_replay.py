from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.events.webhook_operations import create_webhook_delivery_admin_router
from agent_platform.events.webhook_replay import WebhookDeadLetterReplayService
from agent_platform.identity.models import Principal


class FakeOperationsStore:
    def __init__(self):
        self.replayed = []

    async def list_dead_letters(self, tenant_id, *, limit=50):
        return [{"delivery_id": "delivery-a", "tenant_id": tenant_id}]

    async def replay_dead_letter(self, tenant_id, delivery_id):
        self.replayed.append((tenant_id, delivery_id))
        return True


class FakeAudit:
    def __init__(self):
        self.events = []

    async def record(self, **event):
        self.events.append(event)


def build_client(*, replay_service=None, user_id="user-a"):
    store = FakeOperationsStore()
    app = FastAPI()
    app.include_router(create_webhook_delivery_admin_router(
        store=store,
        authorize_manage=lambda actor: actor.user_id == "user-a",
        replay_service=replay_service,
    ))

    @app.middleware("http")
    async def attach_principal(request, call_next):
        setattr(request.state, "identity.principal", Principal(
            user_id=user_id, tenant_id="tenant-a", session_id="session-a",
            email="user@example.test", display_name=user_id,
        ))
        return await call_next(request)

    return TestClient(app), store


class WebhookOperationsReplayRouteTests(unittest.TestCase):
    def test_replay_route_uses_audited_service_and_requires_reason(self):
        store, audit = FakeOperationsStore(), FakeAudit()
        service = WebhookDeadLetterReplayService(
            store=store, audit=audit, authorize=lambda tenant, actor: True,
        )
        client, operations_store = build_client(replay_service=service)
        try:
            missing_reason = client.post(
                "/api/platform/v1/webhooks/dead-letters/delivery-a/replay",
                json={},
            )
            self.assertEqual(missing_reason.status_code, 422)

            response = client.post(
                "/api/platform/v1/webhooks/dead-letters/delivery-a/replay",
                json={"reason": "endpoint recovered"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "pending")
            self.assertEqual(store.replayed, [("tenant-a", "delivery-a")])
            self.assertEqual(operations_store.replayed, [])
            self.assertEqual(audit.events[-1]["outcome"], "succeeded")
        finally:
            client.close()

    def test_replay_route_fails_closed_without_audited_service(self):
        client, store = build_client()
        try:
            response = client.post(
                "/api/platform/v1/webhooks/dead-letters/delivery-a/replay",
                json={"reason": "operator recovery"},
            )
            self.assertEqual(response.status_code, 503)
            self.assertEqual(store.replayed, [])
        finally:
            client.close()

    def test_replay_route_requires_management_permission(self):
        client, store = build_client(user_id="user-b")
        try:
            response = client.post(
                "/api/platform/v1/webhooks/dead-letters/delivery-a/replay",
                json={"reason": "operator recovery"},
            )
            self.assertEqual(response.status_code, 403)
            self.assertEqual(store.replayed, [])
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
