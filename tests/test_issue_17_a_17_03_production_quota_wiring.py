"""Regression checks for production quota composition (no external services required)."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProductionQuotaCompositionTests(unittest.TestCase):
    def test_production_uses_shared_postgres_store_and_service(self):
        source = (ROOT / "agent_platform" / "production.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertIn("PostgresQuotaStore", imports)
        self.assertIn("QuotaService", imports)
        self.assertIn("PostgresQuotaPolicyRepository", imports)
        self.assertIn("QuotaPolicyService", imports)
        self.assertIn("QuotaReservationMaintenanceWorker", imports)
        self.assertNotIn("InMemoryQuotaStore", imports)

        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "PostgresQuotaStore"
        ]
        self.assertEqual(len(calls), 1)
        self.assertTrue(any(
            keyword.arg == "connection_factory"
            and isinstance(keyword.value, ast.Name)
            and keyword.value.id == "connection_factory"
            for keyword in calls[0].keywords
        ))

    def test_production_passes_quota_routes_audit_and_maintenance_lifecycle(self):
        source = (ROOT / "agent_platform" / "production.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        app_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "create_platform_app"
        ]
        self.assertEqual(len(app_calls), 1)
        configured = {keyword.arg for keyword in app_calls[0].keywords}
        self.assertTrue({
            "quota_reporting_store",
            "quota_service",
            "quota_manage_authorizer",
            "quota_scope_authorizer",
            "quota_audit_writer",
            "quota_reservation_worker",
        }.issubset(configured))

        api_source = (ROOT / "agent_platform" / "api" / "app.py").read_text(encoding="utf-8")
        self.assertIn("app.router.on_startup.append(quota_reservation_worker.start)", api_source)
        self.assertIn("app.router.on_shutdown.append(quota_reservation_worker.stop)", api_source)

    def test_quota_mutations_use_append_only_audit_adapter(self):
        source = (ROOT / "agent_platform" / "production.py").read_text(encoding="utf-8")
        self.assertIn("PostgresAuditWriter(connection_factory)", source)
        self.assertIn("quota_policy_service = QuotaPolicyService(quota_policy_repository)", source)
        self.assertIn("await self._writer.record(", source)
        self.assertIn('permission_key="quota.manage"', source)


if __name__ == "__main__":
    unittest.main()
