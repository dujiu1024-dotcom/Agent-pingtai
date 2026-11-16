from __future__ import annotations

import unittest

from agent_platform.security.audit import PostgresAuditWriter, PostgresIdentityAuditReader


class FakeCursor:
    async def fetchall(self):
        return []


class FakeConnection:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, *_args):
        return FakeCursor()


class AuditInputValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_writer_rejects_invalid_identity_action_and_detail_types(self):
        writer = PostgresAuditWriter(lambda: self.fail("invalid input must not connect"))
        valid = dict(tenant_id="tenant-a", actor_id="user-a", action="file.read", subject_id="asset-a")
        for field, value in (
            ("tenant_id", True), ("actor_id", 7), ("subject_id", " "),
            ("action", None), ("action", "BAD ACTION"), ("outcome", []),
            ("detail", ["not", "a", "mapping"]),
        ):
            arguments = {**valid, field: value}
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                await writer.record(**arguments)

    async def test_reader_rejects_invalid_limit_and_filter_types(self):
        reader = PostgresIdentityAuditReader(lambda: self.fail("invalid input must not connect"))
        for kwargs in (
            {"limit": True}, {"limit": 1.5}, {"actor_id": 42},
            {"action": []},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                await reader.list_events(tenant_id="tenant-a", **kwargs)

    async def test_valid_reader_query_uses_connection(self):
        reader = PostgresIdentityAuditReader(lambda: FakeConnection())
        self.assertEqual(await reader.list_events(tenant_id="tenant-a"), [])


if __name__ == "__main__":
    unittest.main()
