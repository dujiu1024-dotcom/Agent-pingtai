from __future__ import annotations

import unittest

from agent_platform.observability.prompt_store import PostgresPromptRegistry
from agent_platform.observability.prompts import PromptRegistry


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows

    async def fetchone(self):
        if isinstance(self.rows, list):
            return self.rows[0] if self.rows else None
        return self.rows

    async def fetchall(self):
        return self.rows or []


class FakePromptVariantDb:
    def __init__(self):
        self.versions = {
            ("tenant:1:t:agent", 1): {},
            ("tenant:1:t:agent", 2): {},
        }
        self.variants = {}
        self.history = []
        self.deployments = {}
        self.audit = []

    async def connect(self):
        return FakePromptVariantConnection(self)


class FakePromptVariantConnection:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def transaction(self):
        return self

    async def execute(self, query, params=()):
        sql = " ".join(query.split())
        if sql.startswith("SELECT 1 FROM platform_prompt_versions"):
            return FakeCursor({} if (params[0], params[1]) in self.db.versions else None)
        if sql.startswith("INSERT INTO platform_prompt_variants"):
            prompt_id, name, version, created_by, updated_by = params
            key = (prompt_id, name)
            if key in self.db.variants:
                raise ValueError("duplicate variant")
            self.db.variants[key] = {
                "prompt_id": prompt_id, "variant_name": name,
                "current_version": version, "status": "active",
                "created_by": created_by, "updated_by": updated_by,
            }
            return FakeCursor()
        if sql.startswith("INSERT INTO platform_prompt_variant_versions"):
            prompt_id, name, version, changed_by = params
            self.db.history.append({
                "prompt_id": prompt_id, "variant_name": name,
                "version": version, "changed_by": changed_by,
                "created_at": len(self.db.history) + 1,
            })
            return FakeCursor()
        if sql.startswith("INSERT INTO platform_prompt_audit_events"):
            prompt_id, action, actor_id, details = params
            self.db.audit.append({
                "prompt_id": prompt_id, "action": action,
                "actor_id": actor_id, "details": details,
            })
            return FakeCursor()
        if sql.startswith("SELECT status, created_by FROM platform_prompt_variants"):
            row = self.db.variants.get((params[0], params[1]))
            return FakeCursor(row)
        if sql.startswith("SELECT current_version,status,created_by FROM platform_prompt_variants"):
            row = self.db.variants.get((params[0], params[1]))
            return FakeCursor(row)
        if sql.startswith("UPDATE platform_prompt_variants SET current_version"):
            version, actor, prompt_id, name = params
            self.db.variants[(prompt_id, name)].update(
                current_version=version, updated_by=actor,
            )
            return FakeCursor()
        if sql.startswith("UPDATE platform_prompt_variants SET status"):
            actor, prompt_id, name = params
            self.db.variants[(prompt_id, name)].update(
                status="deprecated", updated_by=actor,
            )
            return FakeCursor()
        if sql.startswith("SELECT 1 FROM platform_prompt_deployments"):
            prompt_id, active, canary = params
            deployment = self.db.deployments.get(prompt_id)
            if deployment and active in (deployment["active_version"], deployment["canary_version"]):
                return FakeCursor({})
            if deployment and canary in (deployment["active_version"], deployment["canary_version"]):
                return FakeCursor({})
            return FakeCursor(None)
        if "FROM platform_prompt_variants WHERE prompt_id=%s ORDER BY variant_name" in sql:
            prompt_id, = params
            rows = [row.copy() for (pid, _), row in self.db.variants.items() if pid == prompt_id]
            return FakeCursor(sorted(rows, key=lambda row: row["variant_name"]))
        if "FROM platform_prompt_variant_versions" in sql:
            prompt_id, name = params
            rows = [
                row.copy() for row in self.db.history
                if row["prompt_id"] == prompt_id and row["variant_name"] == name
            ]
            return FakeCursor(rows)
        raise AssertionError(f"unhandled SQL: {sql}")


class PromptVariantDomainTests(unittest.TestCase):
    def test_variant_history_and_deprecation(self):
        registry = PromptRegistry()
        first = registry.register("agent", "Hello {name}")
        second = registry.register("agent", "Welcome {name}")
        created = registry.create_variant("agent", "prod", first.version, created_by="admin")
        self.assertEqual(created.current_version, 1)
        assigned = registry.assign_variant("agent", "prod", second.version, updated_by="editor")
        self.assertEqual(assigned.current_version, 2)
        self.assertEqual(registry.variant_history("agent", "prod"), (1, 2))
        deprecated = registry.deprecate_variant("agent", "prod", updated_by="admin")
        self.assertEqual(deprecated.status, "deprecated")
        with self.assertRaises(ValueError):
            registry.assign_variant("agent", "prod", 1, updated_by="editor")
        with self.assertRaises(ValueError):
            registry.create_variant("agent", "Prod", 1, created_by="admin")


class PersistentPromptVariantTests(unittest.IsolatedAsyncioTestCase):
    async def test_persistent_variant_history_and_deprecation(self):
        db = FakePromptVariantDb()
        registry = PostgresPromptRegistry(db.connect)
        created = await registry.create_variant(
            "tenant:1:t:agent", "prod", 1, created_by="admin",
        )
        self.assertEqual(created.current_version, 1)
        assigned = await registry.assign_variant(
            "tenant:1:t:agent", "prod", 2, updated_by="editor",
        )
        self.assertEqual(assigned.current_version, 2)
        variants = await registry.list_variants("tenant:1:t:agent")
        self.assertEqual(variants[0].name, "prod")
        history = await registry.variant_history("tenant:1:t:agent", "prod")
        self.assertEqual([row["version"] for row in history], [1, 2])
        self.assertEqual([event["action"] for event in db.audit], [
            "variant.created", "variant.version_assigned",
        ])
        deprecated = await registry.deprecate_variant(
            "tenant:1:t:agent", "prod", updated_by="admin",
        )
        self.assertEqual(deprecated.status, "deprecated")
        with self.assertRaises(ValueError):
            await registry.assign_variant(
                "tenant:1:t:agent", "prod", 1, updated_by="editor",
            )
        self.assertEqual(db.audit[-1]["action"], "variant.deprecated")

    async def test_deployed_variant_cannot_be_deprecated(self):
        db = FakePromptVariantDb()
        registry = PostgresPromptRegistry(db.connect)
        await registry.create_variant("tenant:1:t:agent", "prod", 1, created_by="admin")
        db.deployments["tenant:1:t:agent"] = {
            "active_version": 1, "canary_version": None,
        }
        with self.assertRaises(ValueError):
            await registry.deprecate_variant(
                "tenant:1:t:agent", "prod", updated_by="admin",
            )


if __name__ == "__main__":
    unittest.main()
