from __future__ import annotations

import unittest

from agent_platform.observability.trace_context import current_trace_context
from agent_platform.observability.trace_middleware import TraceContextASGIMiddleware


class TraceContextMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, headers, *, app_error=False):
        observed = {}
        messages = []

        async def app(scope, receive, send):
            observed["context"] = current_trace_context()
            observed["state"] = scope["state"]
            if app_error:
                raise RuntimeError("request failed")
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({"type": "http.response.body", "body": b"{}"})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        middleware = TraceContextASGIMiddleware(app)
        scope = {"type": "http", "method": "POST", "path": "/api/runs", "headers": headers, "state": {}}
        await middleware(scope, receive, send)
        return observed, messages

    async def test_creates_and_echoes_trace_context(self):
        observed, messages = await self.invoke([])
        context = observed["context"]
        self.assertIsNotNone(context)
        response_headers = dict(messages[0]["headers"])
        self.assertEqual(response_headers[b"traceparent"].decode(), context.traceparent)
        self.assertEqual(observed["state"]["trace_context"], context)
        self.assertEqual(observed["state"]["trace_span"].component, "request")
        self.assertEqual(observed["state"]["trace_span"].status, "ok")
        self.assertIsNone(current_trace_context())

    async def test_extracts_valid_context_and_preserves_correlation(self):
        traceparent = b"00-1234567890abcdef1234567890abcdef-1234567890abcdef-01"
        observed, messages = await self.invoke([
            (b"traceparent", traceparent),
            (b"x-correlation-id", b"request-17"),
            (b"x-request-id", b"req-17"),
        ])
        self.assertEqual(observed["context"].traceparent, traceparent.decode())
        self.assertEqual(observed["context"].correlation_id, "request-17")
        self.assertEqual(observed["state"]["request_id"], "req-17")
        self.assertEqual(dict(messages[0]["headers"])[b"x-correlation-id"], b"request-17")

    async def test_malformed_trace_is_replaced_not_reflected(self):
        observed, messages = await self.invoke([
            (b"traceparent", b"untrusted-malformed"),
            (b"x-correlation-id", b"request-18"),
        ])
        response_headers = dict(messages[0]["headers"])
        self.assertNotEqual(response_headers[b"traceparent"], b"untrusted-malformed")
        self.assertEqual(observed["context"].correlation_id, "request-18")

    async def test_context_is_restored_when_application_raises(self):
        async def app(scope, receive, send):
            self.assertIsNotNone(current_trace_context())
            raise RuntimeError("request failed")

        middleware = TraceContextASGIMiddleware(app)
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            pass
        with self.assertRaisesRegex(RuntimeError, "request failed"):
            await middleware({"type": "http", "headers": []}, receive, send)
        self.assertIsNone(current_trace_context())

    async def test_non_http_scope_is_forwarded_without_trace_mutation(self):
        observed = {}
        async def app(scope, receive, send):
            observed["scope"] = scope
        async def receive():
            return {}
        async def send(message):
            pass
        scope = {"type": "lifespan"}
        await TraceContextASGIMiddleware(app)(scope, receive, send)
        self.assertIs(observed["scope"], scope)


if __name__ == "__main__":
    unittest.main()
