from __future__ import annotations

import unittest

from agent_platform.observability.prompts import PromptRegistry


class PromptRegistryListingTests(unittest.TestCase):
    def test_list_prompts_returns_only_keys_with_the_exact_tenant_prefix(self) -> None:
        registry = PromptRegistry()
        registry.register("tenant:3:org:shared", "hello {name}", created_by="user-a")
        registry.register("tenant:3:org:billing", "invoice {number}", created_by="user-a")
        registry.register("tenant:4:org2:shared", "private {value}", created_by="user-b")

        rows = registry.list_prompts(prefix="tenant:3:org:", limit=20)

        self.assertEqual([row["prompt_id"] for row in rows], ["billing", "shared"])
        self.assertTrue(all(row["versions_count"] == 1 for row in rows))
        self.assertTrue(all("template" not in row for row in rows))

    def test_list_prompts_reports_latest_active_and_canary_versions(self) -> None:
        registry = PromptRegistry()
        key = "tenant:3:org:welcome"
        registry.register(key, "hello {name}", created_by="user-a")
        registry.register(key, "welcome {name}", created_by="user-a")
        registry.activate(key, 1, updated_by="user-a", evaluation_run_id="eval-101")
        registry.set_canary(
            key, 2, 10, updated_by="user-a", evaluation_run_id="eval-102",
        )

        row = registry.list_prompts(prefix="tenant:3:org:")[0]

        self.assertEqual(row["latest_version"], 2)
        self.assertEqual(row["active_version"], 1)
        self.assertEqual(row["canary_version"], 2)
        self.assertEqual(row["canary_percentage"], 10)
        self.assertEqual(row["versions_count"], 2)
        canary_event = registry.list_audit(key)[-1]
        self.assertEqual(canary_event.action, "canary.configured")
        self.assertEqual(dict(canary_event.details)["evaluation_run_id"], "eval-102")

    def test_list_prompts_rejects_invalid_limits(self) -> None:
        registry = PromptRegistry()
        for limit in (0, -1, 201, True, 1.5):
            with self.subTest(limit=limit):
                with self.assertRaisesRegex(ValueError, "limit must be between 1 and 200"):
                    registry.list_prompts(prefix="tenant:3:org:", limit=limit)  # type: ignore[arg-type]

    def test_list_prompts_rejects_missing_tenant_prefix(self) -> None:
        with self.assertRaisesRegex(ValueError, "tenant prompt prefix is required"):
            PromptRegistry().list_prompts(prefix="")
