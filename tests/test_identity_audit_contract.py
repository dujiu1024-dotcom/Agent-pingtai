from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent_platform.identity.postgres import PostgresIdentityRepository


class Cursor:
    def __init__(self, row=None):
        self.row = row

    async def fetchone(self):
        return self.row


class Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class Connection:
    def __init__(self):
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def transaction(self):
        return Transaction()

    async def execute(self, query, params=()):
        self.statements.append((query, params))
        if "RETURNING session_id" in query:
            return Cursor({"session_id": params[0] if params else "session-1"})
        return Cursor()


class IdentityAuditTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.connection = Connection()

        async def connect():
            return self.connection

        self.repository = PostgresIdentityRepository(connect)

    async def test_successful_login_audit_excludes_session_token_material(self):
        await self.repository.create_session(
            session_id="session-1", tenant_id="tenant-a", user_id="user-a",
            token_hash="do-not-audit-token-hash",
            expires_at=datetime.now(timezone.utc),
        )
        audit = [x for x in self.connection.statements if "platform_identity_audit" in x[0]]
        self.assertEqual(len(audit), 1)
        query, params = audit[0]
        self.assertIn("'auth.login'", query)
        self.assertIn("session_id", query)
        self.assertNotIn("token_hash", query)
        self.assertNotIn("do-not-audit-token-hash", repr(params))

    async def test_successful_logout_audit_is_written_only_after_revocation(self):
        changed = await self.repository.revoke_session(
            session_id="session-1", tenant_id="tenant-a", user_id="user-a",
            revoked_at=datetime.now(timezone.utc),
        )
        self.assertTrue(changed)
        audit = [x for x in self.connection.statements if "platform_identity_audit" in x[0]]
        self.assertEqual(len(audit), 1)
        self.assertIn("'auth.logout'", audit[0][0])


if __name__ == "__main__":
    unittest.main()
