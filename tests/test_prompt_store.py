from __future__ import annotations

import unittest

from agent_platform.observability.prompt_store import PostgresPromptRegistry


class FakeCursor:
    def __init__(self, row=None):
        self.row = row
    async def fetchone(self):
        return self.row
    async def fetchall(self):
        return self.row or []


class FakePromptConnection:
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
        if sql.startswith("INSERT INTO platform_prompt_audit_events"):
            prompt_id, action, actor_id, details = params
            self.db.audit.append({"event_id": len(self.db.audit) + 1,
                "prompt_id": prompt_id, "action": action, "actor_id": actor_id,
                "details": details, "created_at": "now"})
            return FakeCursor()
        if "FROM platform_prompt_audit_events" in sql:
            prompt_id, limit = params
            return FakeCursor(list(reversed([x for x in self.db.audit if x["prompt_id"] == prompt_id]))[:limit])
        if "pg_advisory_xact_lock" in sql:
            return FakeCursor()
        if "COALESCE(MAX(version)" in sql:
            prompt_id, = params
            versions = self.db.versions.get(prompt_id, {})
            return FakeCursor({"next_version": max(versions, default=0) + 1})
        if sql.startswith("INSERT INTO platform_prompt_versions"):
            prompt_id, version, template, variables, digest, created_by = params
            self.db.versions.setdefault(prompt_id, {})[version] = {
                "prompt_id": prompt_id, "version": version, "template": template,
                "variables": variables, "content_hash": digest, "created_by": created_by,
            }
            return FakeCursor()
        if sql.startswith("SELECT 1 FROM platform_prompt_versions"):
            prompt_id, version = params
            return FakeCursor({} if version in self.db.versions.get(prompt_id, {}) else None)
        if sql.startswith("INSERT INTO platform_prompt_deployments"):
            prompt_id, version, updated_by = params
            self.db.deployments[prompt_id] = {
                "active_version": version, "canary_version": None,
                "canary_percentage": 0, "updated_by": updated_by,
            }
            return FakeCursor()
        if "SELECT d.active_version FROM platform_prompt_deployments" in sql:
            candidate, prompt_id = params
            dep = self.db.deployments.get(prompt_id)
            exists = candidate in self.db.versions.get(prompt_id, {})
            return FakeCursor({"active_version": dep["active_version"]} if dep and exists else None)
        if sql.startswith("UPDATE platform_prompt_deployments"):
            candidate, percentage, updated_by, prompt_id = params
            dep = self.db.deployments[prompt_id]
            dep.update(canary_version=candidate, canary_percentage=percentage, updated_by=updated_by)
            return FakeCursor()
        if "SELECT active_version,canary_version,canary_percentage" in sql:
            prompt_id, = params
            return FakeCursor(self.db.deployments.get(prompt_id))
        if "FROM platform_prompt_versions WHERE prompt_id=%s AND version=%s" in sql:
            prompt_id, version = params
            return FakeCursor(self.db.versions.get(prompt_id, {}).get(version))
        raise AssertionError(f"unhandled SQL: {sql}")


class FakePromptDb:
    def __init__(self):
        self.versions = {}
        self.deployments = {}
        self.audit = []
    async def connect(self):
        return FakePromptConnection(self)


class PersistentPromptRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_versions_canary_render_and_rollback_are_persistent(self):
        db = FakePromptDb()
        registry = PostgresPromptRegistry(db.connect)
        first = await registry.register("support", "Hello {name}", created_by="admin")
        second = await registry.register("support", "Welcome {name}", created_by="admin")
        await registry.activate("support", first.version, updated_by="admin", evaluation_run_id="eval-1")
        await registry.set_canary("support", second.version, 100, updated_by="admin", evaluation_run_id="eval-2")
        rendered, selected = await registry.render("support", {"name": "Ada"}, rollout_key="stable")
        self.assertEqual(rendered, "Welcome Ada")
        self.assertEqual(selected.version, second.version)
        await registry.rollback("support", first.version, updated_by="operator", evaluation_run_id="eval-3")
        rendered, selected = await registry.render("support", {"name": "Ada"})
        self.assertEqual(rendered, "Hello Ada")
        self.assertEqual(selected.version, first.version)
        actions = [event["action"] for event in db.audit]
        self.assertEqual(actions, [
            "version.registered", "version.registered", "version.activated",
            "canary.configured", "version.rolled_back",
        ])
        history = await registry.list_audit("support", limit=10)
        self.assertEqual(history[0]["action"], "version.rolled_back")
        self.assertEqual(history[0]["actor_id"], "operator")
        self.assertEqual(history[0]["details"], '{"active_version": 1, "evaluation_run_id": "eval-3"}')

    async def test_variable_contract_and_unknown_versions_fail_closed(self):
        db = FakePromptDb()
        registry = PostgresPromptRegistry(db.connect)
        await registry.register("agent", "Do {task}", created_by="admin")
        await registry.activate("agent", 1, updated_by="admin")
        with self.assertRaises(ValueError):
            await registry.render("agent", {"wrong": "x"})
        with self.assertRaises(KeyError):
            await registry.resolve("agent", version=99)


if __name__ == "__main__":
    unittest.main()
