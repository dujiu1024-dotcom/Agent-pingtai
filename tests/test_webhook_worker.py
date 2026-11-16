from __future__ import annotations

import asyncio
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.events.webhook_worker import WebhookDeliveryWorker
from agent_platform.events.webhook_operations import create_webhook_delivery_admin_router
from agent_platform.events.webhook_replay import WebhookDeadLetterReplayService
from agent_platform.identity.models import Principal


class FakeDispatcher:
    def __init__(self, started=None):
        self.calls = 0
        self.started = started
    async def dispatch(self, *, limit):
        self.calls += 1
        if self.started is not None:
            self.started.set()
        return {"claimed": 0}


class FakeOperations:
    def __init__(self):
        self.items = [{"delivery_id": "delivery-1"}]
        self.replayed = []
    async def list_dead_letters(self, tenant_id, *, limit):
        return self.items[:limit] if tenant_id == "tenant-a" else []
    async def replay_dead_letter(self, tenant_id, delivery_id):
        if tenant_id != "tenant-a" or delivery_id != "00000000-0000-0000-0000-000000000001":
            return False
        self.replayed.append((tenant_id, delivery_id))
        return True


class WebhookWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_starts_stops_and_dispatches(self):
        started = asyncio.Event()
        dispatcher = FakeDispatcher(started)
        worker = WebhookDeliveryWorker(
            dispatcher=dispatcher, interval_seconds=0.1, batch_limit=7,
        )
        await worker.start()
        first_task = worker._task
        await worker.start()
        self.assertIs(worker._task, first_task)
        await asyncio.wait_for(started.wait(), timeout=1)
        await worker.stop()
        self.assertGreaterEqual(dispatcher.calls, 1)
        calls = dispatcher.calls
        await asyncio.sleep(0.02)
        self.assertEqual(dispatcher.calls, calls)
        restarted = asyncio.Event()
        dispatcher.started = restarted
        await worker.start()
        await asyncio.wait_for(restarted.wait(), timeout=1)
        await worker.stop()
        self.assertGreater(dispatcher.calls, calls)

    async def test_worker_configuration_is_bounded_and_type_safe(self):
        for interval in (0, True, "1"):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                WebhookDeliveryWorker(dispatcher=FakeDispatcher(), interval_seconds=interval)
        for limit in (0, 501, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                WebhookDeliveryWorker(dispatcher=FakeDispatcher(), batch_limit=limit)

    async def test_worker_does_not_log_provider_exception_text(self):
        class SecretFailureDispatcher:
            async def dispatch(self, *, limit):
                raise RuntimeError("destination=https://private.example/token=secret")

        worker = WebhookDeliveryWorker(
            dispatcher=SecretFailureDispatcher(), interval_seconds=0.1,
        )
        with self.assertLogs("agent_platform.events.webhook_worker", level="WARNING") as captured:
            await worker.start()
            await asyncio.sleep(0)
            await worker.stop()
        self.assertTrue(any("RuntimeError" in line for line in captured.output))
        self.assertFalse(any("private.example" in line or "secret" in line for line in captured.output))

    async def test_worker_recovers_after_dispatch_failure(self):
        recovered = asyncio.Event()

        class FlakyDispatcher:
            def __init__(self):
                self.calls = 0
            async def dispatch(self, *, limit):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("must not be logged")
                recovered.set()
                return {"claimed": 1}

        dispatcher = FlakyDispatcher()
        worker = WebhookDeliveryWorker(
            dispatcher=dispatcher, interval_seconds=0.1, batch_limit=3,
        )
        await worker.start()
        try:
            await asyncio.wait_for(recovered.wait(), timeout=1)
        finally:
            await worker.stop()
        self.assertGreaterEqual(dispatcher.calls, 2)


class WebhookDeadLetterApiTests(unittest.TestCase):
    def test_dead_letter_list_and_replay_are_tenant_scoped_and_authorized(self):
        store = FakeOperations()

        class Audit:
            def __init__(self):
                self.events = []

            async def record(self, **event):
                self.events.append(event)

        audit = Audit()
        replay_service = WebhookDeadLetterReplayService(
            store=store,
            audit=audit,
            authorize=lambda tenant_id, actor_id: (
                tenant_id == "tenant-a" and actor_id == "admin"
            ),
        )
        app = FastAPI()
        app.include_router(create_webhook_delivery_admin_router(
            store=store,
            authorize_manage=lambda actor: actor.user_id == "admin",
            replay_service=replay_service,
        ))

        @app.middleware("http")
        async def attach_principal(request, call_next):
            setattr(request.state, "identity.principal", Principal(
                user_id="admin", tenant_id="tenant-a", session_id="s",
                email="admin@example.test", display_name="admin",
            ))
            return await call_next(request)

        with TestClient(app) as client:
            listed = client.get("/api/platform/v1/webhooks/dead-letters")
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(len(listed.json()["items"]), 1)
            replay = client.post(
                "/api/platform/v1/webhooks/dead-letters/00000000-0000-0000-0000-000000000001/replay",
                json={"reason": "operator recovery"},
            )
            self.assertEqual(replay.status_code, 200)
            self.assertEqual(store.replayed, [
                ("tenant-a", "00000000-0000-0000-0000-000000000001"),
            ])
            self.assertEqual(audit.events[-1]["outcome"], "succeeded")
            self.assertEqual(audit.events[-1]["action"], "webhook.dead_letter.replay")

    def test_unauthorized_dead_letter_access_is_denied(self):
        app = FastAPI()
        app.include_router(create_webhook_delivery_admin_router(
            store=FakeOperations(), authorize_manage=lambda _actor: False,
        ))

        @app.middleware("http")
        async def attach_principal(request, call_next):
            setattr(request.state, "identity.principal", Principal(
                user_id="user", tenant_id="tenant-a", session_id="s",
                email="user@example.test", display_name="user",
            ))
            return await call_next(request)

        with TestClient(app) as client:
            self.assertEqual(client.get("/api/platform/v1/webhooks/dead-letters").status_code, 403)


if __name__ == "__main__":
    unittest.main()
