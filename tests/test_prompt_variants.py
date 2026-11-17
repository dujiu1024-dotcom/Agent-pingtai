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


    def test_active_and_canary_variants_cannot_be_deprecated(self):
        registry = PromptRegistry()
        active = registry.register("agent", "Stable {name}")
        candidate = registry.register("agent", "Candidate {name}")
        registry.create_variant("agent", "prod", active.version, created_by="admin")
        registry.create_variant("agent", "candidate", candidate.version, created_by="admin")
        registry.activate("agent", active.version)
        registry.set_canary("agent", candidate.version, 10)

        with self.assertRaisesRegex(ValueError, "deployed prompt variant"):
            registry.deprecate_variant("agent", "prod", updated_by="admin")
        with self.assertRaisesRegex(ValueError, "deployed prompt variant"):
            registry.deprecate_variant("agent", "candidate", updated_by="admin")

        self.assertEqual(
            [variant.status for variant in registry.list_variants("agent")],
            ["active", "active"],
        )

    def test_in_memory_audit_traces_version_variant_deprecation_and_rollback(self):
        registry = PromptRegistry()
        first = registry.register("agent", "Stable", created_by="author")
        second = registry.register("agent", "Candidate", created_by="author")
        third = registry.register("agent", "Archived", created_by="author")
        registry.create_variant("agent", "candidate", third.version, created_by="author")
        registry.activate("agent", first.version, updated_by="release-manager")
        registry.assign_variant("agent", "candidate", second.version, updated_by="editor")
        registry.rollback("agent", first.version, updated_by="operator")
        registry.deprecate_variant("agent", "candidate", updated_by="operator")

        events = registry.list_audit("agent")
        self.assertEqual(
            [event.action for event in events],
            [
                "version.registered", "version.registered", "version.registered",
                "variant.created", "version.activated", "variant.version_assigned",
                "version.rolled_back", "variant.deprecated",
            ],
        )
        self.assertEqual(
            [event.event_id for event in events],
            list(range(1, len(events) + 1)),
        )
        self.assertEqual(events[0].actor_id, "author")
        self.assertEqual(events[6].actor_id, "operator")
        self.assertEqual(dict(events[6].details), {"active_version": 1})
        self.assertEqual(registry.resolve("agent").version, first.version)
        self.assertEqual(registry.variant_history("agent", "candidate"), (3, 2))
        self.assertEqual(registry.list_variants("agent")[0].status, "deprecated")
        self.assertEqual(len(registry.list_audit("other")), 0)
        self.assertEqual(len(registry.list_audit("agent", limit=2)), 2)

    def test_register_rejects_non_string_prompt_id_and_template(self):
        registry = PromptRegistry()
        for prompt_id, template in ((None, "Hello"), ("agent", None), ("", "Hello"), ("agent", "")):
            with self.subTest(prompt_id=prompt_id, template=template):
                with self.assertRaisesRegex(ValueError, "prompt_id and template are required"):
                    registry.register(prompt_id, template)
        self.assertEqual(registry.list_versions("agent"), ())
        self.assertEqual(registry.list_audit("agent"), ())

    def test_variant_version_must_be_positive_integer(self):
        registry = PromptRegistry()
        registry.register("agent", "Hello")
        with self.assertRaisesRegex(ValueError, "positive integer"):
            registry.create_variant("agent", "prod", True, created_by="admin")
        registry.create_variant("agent", "prod", 1, created_by="admin")
        with self.assertRaisesRegex(ValueError, "positive integer"):
            registry.assign_variant("agent", "prod", True, updated_by="admin")


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
