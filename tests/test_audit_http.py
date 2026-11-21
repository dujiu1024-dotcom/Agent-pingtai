from __future__ import annotations

import unittest

from agent_platform.security.audit_context import AuditActor
from agent_platform.security.audit_http import AuditRequestASGIMiddleware


class FakeAuditSink:
    def __init__(self, fail=False):
        self.events = []
        self.fail = fail

    async def record(self, **event):
        if self.fail:
            raise RuntimeError("audit store unavailable")
        self.events.append(event)


class AuditRequestMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, *, status=200, sink=None, app_error=False):
        sink = sink or FakeAuditSink()
        called = []

        async def app(scope, receive, send):
            called.append(True)
            if app_error:
                raise RuntimeError("handler failed")
            await send({"type": "http.response.start", "status": status, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            pass

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/agents/agent-a/run",
            "state": {
                "identity.principal": object(),
                "correlation_id": "corr-a",
            },
        }
        middleware = AuditRequestASGIMiddleware(
            app,
            sink=sink,
            resolve_actor=lambda principal: AuditActor(
                "tenant-a", "user-a", "request-a",
            ),
        )
        await middleware(scope, receive, send)
        return sink, called

    async def test_records_intent_before_completion_with_authenticated_actor(self):
        sink, called = await self.invoke(status=201)
        self.assertEqual(called, [True])
        self.assertEqual([event["action"] for event in sink.events], [
            "http.request.requested", "http.request",
        ])
        self.assertEqual(sink.events[0]["tenant_id"], "tenant-a")
        self.assertEqual(sink.events[0]["actor_id"], "user-a")
        self.assertEqual(sink.events[1]["outcome"], "succeeded")
        self.assertEqual(sink.events[1]["detail"]["status_code"], 201)

    async def test_maps_client_and_server_failures_to_audit_outcomes(self):
        denied, _ = await self.invoke(status=403)
        failed, _ = await self.invoke(status=503)
        self.assertEqual(denied.events[-1]["outcome"], "denied")
        self.assertEqual(failed.events[-1]["outcome"], "failed")

    async def test_audit_intent_failure_prevents_application_side_effects(self):
        sink = FakeAuditSink(fail=True)
        with self.assertRaisesRegex(RuntimeError, "audit store unavailable"):
            await self.invoke(sink=sink)
        self.assertEqual(sink.events, [])

    async def test_records_failed_handler_before_reraising(self):
        sink = FakeAuditSink()
        # Invoke the middleware directly because the helper's app_error flag
        # intentionally propagates the original handler exception.
        async def app(scope, receive, send):
            raise RuntimeError("handler failed")
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            pass
        middleware = AuditRequestASGIMiddleware(
            app, sink=sink,
            resolve_actor=lambda principal: AuditActor("tenant-a", "user-a"),
        )
        with self.assertRaisesRegex(RuntimeError, "handler failed"):
            await middleware(
                {"type": "http", "method": "DELETE", "path": "/api/files/file-a",
                 "state": {"identity.principal": object()}},
                receive, send,
            )
        self.assertEqual(sink.events[-1]["outcome"], "failed")

    async def test_skips_anonymous_request_without_fabricating_identity(self):
        sink = FakeAuditSink()
        called = []
        async def app(scope, receive, send):
            called.append(True)
        async def receive():
            return {}
        async def send(message):
            pass
        middleware = AuditRequestASGIMiddleware(
            app, sink=sink, resolve_actor=lambda principal: None,
        )
        await middleware(
            {"type": "http", "method": "GET", "path": "/health", "state": {}},
            receive, send,
        )
        self.assertEqual(called, [True])
        self.assertEqual(sink.events, [])

    async def test_authenticated_principal_without_actor_fails_closed(self):
        sink = FakeAuditSink()
        called = []

        async def app(scope, receive, send):
            called.append(True)

        async def receive():
            return {}

        async def send(message):
            pass

        middleware = AuditRequestASGIMiddleware(
            app, sink=sink, resolve_actor=lambda principal: None,
        )
        with self.assertRaisesRegex(RuntimeError, "could not be resolved to an audit actor"):
            await middleware(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/api/agents/agent-a/run",
                    "state": {"identity.principal": object()},
                },
                receive,
                send,
            )
        self.assertEqual(called, [])
        self.assertEqual(sink.events, [])
 
    async def test_instance_request_is_audited_with_null_actor(self):
        sink = FakeAuditSink()
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            pass
        middleware = AuditRequestASGIMiddleware(
            app, sink=sink, resolve_actor=lambda principal: None,
            resolve_null_actor_tenant=lambda scope: "instance-a",
        )
        await middleware(
            {"type": "http", "method": "GET", "path": "/api/platform/v1/office/state", "state": {}},
            receive, send,
        )
        self.assertEqual(len(sink.events), 2)
        self.assertIsNone(sink.events[0]["actor_id"])
        self.assertIsNone(sink.events[1]["actor_id"])
        self.assertEqual(sink.events[0]["tenant_id"], "instance-a")


if __name__ == "__main__":
    unittest.main()
