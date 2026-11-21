"""Acceptance tests for immutable audit records and fail-closed writes."""

from __future__ import annotations

import unittest
from pathlib import Path

from agent_platform.api.audit import create_audit_router
from agent_platform.approval.postgres import PostgresAuditSink
from agent_platform.security.audit import PostgresAuditWriter
from agent_platform.security.audit_context import AuditActor
from agent_platform.security.audit_http import AuditRequestASGIMiddleware

ROOT = Path(__file__).resolve().parents[1]


class AuditBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_identity_audit_writer_failure_is_not_swallowed(self):
        async def unavailable():
            raise RuntimeError("audit database unavailable")

        writer = PostgresAuditWriter(unavailable)
        with self.assertRaisesRegex(RuntimeError, "audit database unavailable"):
            await writer.record(
                tenant_id="tenant-a", actor_id="agent-a", action="agent.run",
                subject_id="run-a", outcome="started",
            )

    async def test_tool_audit_sink_failure_is_not_swallowed(self):
        async def unavailable():
            raise RuntimeError("audit database unavailable")

        sink = PostgresAuditSink(unavailable)
        with self.assertRaisesRegex(RuntimeError, "audit database unavailable"):
            await sink.record(
                tenant_id="tenant-a", actor_id="agent-a", action="tool.invoke",
                resource="file-a", action_digest="a" * 64, outcome="started",
            )

    async def test_write_ahead_failure_blocks_the_application_handler(self):
        calls = []

        async def app(scope, receive, send):
            calls.append("side-effect")

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(_message):
            pass

        class UnavailableSink:
            async def record(self, **_event):
                raise RuntimeError("audit database unavailable")

        middleware = AuditRequestASGIMiddleware(
            app, sink=UnavailableSink(),
            resolve_actor=lambda _principal: AuditActor("tenant-a", "agent-a"),
        )
        with self.assertRaisesRegex(RuntimeError, "audit database unavailable"):
            await middleware(
                {
                    "type": "http", "method": "POST", "path": "/api/agents/a/run",
                    "state": {"identity.principal": object()},
                },
                receive, send,
            )
        self.assertEqual(calls, [])

    async def test_completion_audit_failure_is_visible_and_does_not_retry_handler(self):
        calls = []

        class FailOnCompletion:
            def __init__(self):
                self.writes = 0

            async def record(self, **_event):
                self.writes += 1
                if self.writes == 2:
                    raise RuntimeError("completion audit unavailable")

        async def app(scope, receive, send):
            calls.append("executed")
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(_message):
            pass

        sink = FailOnCompletion()
        middleware = AuditRequestASGIMiddleware(
            app, sink=sink,
            resolve_actor=lambda _principal: AuditActor("tenant-a", "agent-a"),
        )
        with self.assertRaisesRegex(RuntimeError, "completion audit unavailable"):
            await middleware(
                {
                    "type": "http", "method": "POST", "path": "/api/agents/a/run",
                    "state": {"identity.principal": object()},
                },
                receive, send,
            )
        self.assertEqual(calls, ["executed"])
        self.assertEqual(sink.writes, 2)

    def test_agent_audit_query_surface_is_read_only(self):
        router = create_audit_router(
            reader=object(), identity_reader=object(),
            authorize_read=lambda _principal: True,
        )
        audit_routes = [
            route for route in router.routes
            if getattr(route, "path", "").startswith("/api/platform/v1/audit/")
        ]
        self.assertTrue(audit_routes)
        self.assertTrue(all(route.methods == {"GET"} for route in audit_routes))

    def test_postgres_migration_enforces_append_only_and_separate_ownership(self):
        migration = (
            ROOT / "agent_platform/storage/postgres/015_audit_append_only.sql"
        ).read_text(encoding="utf-8")
        for table in ("platform_tool_audit", "platform_identity_audit"):
            self.assertIn(f"BEFORE UPDATE OR DELETE ON {table}", migration)
            self.assertIn(f"BEFORE TRUNCATE ON {table}", migration)
            self.assertIn(
                f"REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM PUBLIC",
                migration,
            )

        policy = (
            ROOT / "docs/security/audit-failure-policy.md"
        ).read_text(encoding="utf-8")
        self.assertIn("must not own", policy)
        self.assertIn("SELECT", policy)
        self.assertIn("INSERT", policy)
        self.assertIn("write-ahead", policy)
        self.assertIn("must not be retried", policy)


if __name__ == "__main__":
    unittest.main()
