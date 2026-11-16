from __future__ import annotations

import unittest

from agent_platform.approval.postgres import PostgresAuditSink


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def transaction(self):
        return FakeTransaction()

    async def execute(self, query, params):
        self.calls.append((query, params))


class PostgresAuditSinkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.connection = FakeConnection()

        async def connect():
            return self.connection

        self.sink = PostgresAuditSink(connect)

    async def test_maps_extended_lifecycle_outcomes_to_database_constraint(self):
        cases = (
            ("replayed", "succeeded", "event=replayed"),
            ("reconciliation_started", "started", "event=reconciliation_started"),
            ("reconciliation_completed", "succeeded", "event=reconciliation_completed"),
        )
        for outcome, expected, detail_prefix in cases:
            await self.sink.record(
                tenant_id="tenant-a", actor_id="operator-a",
                action="tool.lifecycle", resource="run-a/call-a",
                action_digest="a" * 64, outcome=outcome,
                detail="safe note",
            )
            params = self.connection.calls[-1][1]
            self.assertEqual(params[5], expected)
            self.assertTrue(params[8].startswith(detail_prefix))
            self.assertIn("safe note", params[8])

    async def test_preserves_database_native_outcomes(self):
        for outcome in ("denied", "started", "succeeded", "failed"):
            await self.sink.record(
                tenant_id="tenant-a", actor_id="agent-a",
                action="tool.call", resource="doc:1",
                action_digest="b" * 64, outcome=outcome,
            )
            self.assertEqual(self.connection.calls[-1][1][5], outcome)

    async def test_rejects_unknown_outcome_before_database_write(self):
        with self.assertRaisesRegex(ValueError, "unsupported"):
            await self.sink.record(
                tenant_id="tenant-a", actor_id="agent-a",
                action="tool.call", resource="doc:1",
                action_digest="c" * 64, outcome="unknown-state",
            )
        self.assertEqual(self.connection.calls, [])


if __name__ == "__main__":
    unittest.main()
