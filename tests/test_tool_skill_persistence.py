from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent_platform.control_plane.skill_dependency_review import (
    PostgresSkillDependencyReviewStore, SkillDependencyReview,
)
from agent_platform.tools.mcp_health import (
    MCPHealthRecord, PostgresMCPHealthStore,
)


class Cursor:
    def __init__(self, row=None):
        self.row = row

    async def fetchone(self):
        return self.row


class Connection:
    def __init__(self, row=None):
        self.row = row
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, statement, parameters):
        self.statements.append((statement, parameters))
        return Cursor(self.row)


class PostgresGovernanceStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_health_store_persists_and_reads_tenant_scoped_record(self):
        now = datetime.now(timezone.utc)
        row = {
            "tenant_id": "tenant-a", "server_id": "mcp-docs",
            "status": "unhealthy", "checked_at": now, "latency_ms": 14,
            "expected_tools": ["read"], "discovered_tools": ["write"],
            "error_code": "tool_inventory_mismatch",
        }
        connection = Connection(row)
        async def connect():
            return connection

        store = PostgresMCPHealthStore(connect)
        record = MCPHealthRecord(
            tenant_id="tenant-a", server_id="mcp-docs", status="unhealthy",
            checked_at=now, latency_ms=14, expected_tools=("read",),
            discovered_tools=("write",), error_code="tool_inventory_mismatch",
        )
        await store.save(record)
        loaded = await store.get("tenant-a", "mcp-docs")
        if loaded is None:
            self.fail("MCP health record was not loaded")
        self.assertEqual(loaded.tenant_id, "tenant-a")
        self.assertEqual(loaded.error_code, "tool_inventory_mismatch")
        self.assertIn("platform_mcp_health_checks", connection.statements[0][0])
        self.assertIn("WHERE tenant_id=%s AND server_id=%s", connection.statements[1][0])
        self.assertEqual(connection.statements[1][1], ("tenant-a", "mcp-docs"))

    async def test_skill_review_store_persists_digest_findings_and_tenant_scope(self):
        now = datetime.now(timezone.utc)
        row = {
            "tenant_id": "tenant-a", "skill_id": "summarize", "skill_version": 4,
            "manifest_digest": "a" * 64, "status": "rejected",
            "dependencies": {"safe-parser": "==1.2.3"},
            "findings": ["dependency_advisory:safe-parser:CVE-TEST"],
            "reviewed_by": "security-admin", "reviewed_at": now,
        }
        connection = Connection(row)
        async def connect():
            return connection

        store = PostgresSkillDependencyReviewStore(connect)
        review = SkillDependencyReview(
            tenant_id="tenant-a", skill_id="summarize", skill_version=4,
            manifest_digest="a" * 64, status="rejected",
            dependencies={"safe-parser": "==1.2.3"},
            findings=("dependency_advisory:safe-parser:CVE-TEST",),
            reviewed_by="security-admin", reviewed_at=now,
        )
        await store.save(review)
        loaded = await store.get("tenant-a", "summarize", 4)
        if loaded is None:
            self.fail("Skill review was not loaded")
        self.assertEqual(loaded.manifest_digest, "a" * 64)
        self.assertEqual(loaded.findings, review.findings)
        self.assertIn("platform_skill_dependency_reviews", connection.statements[0][0])
        self.assertIn("WHERE tenant_id=%s AND skill_id=%s AND skill_version=%s", connection.statements[1][0])
        self.assertEqual(connection.statements[1][1], ("tenant-a", "summarize", 4))


if __name__ == "__main__":
    unittest.main()
