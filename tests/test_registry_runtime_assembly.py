from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from agent_platform.approval.policy import RiskLevel
from agent_platform.control_plane.registry import RegistryVersion
from agent_platform.control_plane.skill_dependency_review import (
    InMemorySkillDependencyReviewStore,
    SkillDependencyReviewer,
)
from agent_platform.runtime.registry_runtime import RegistryRuntimeAssembler, RegistryRuntimeError
from agent_platform.tools.gateway import ToolDefinition


class SnapshotService:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    async def get(self, *, tenant_id, run_id):
        return self.snapshot


class ModelRouter:
    class Estimator:
        def estimate(self, **kwargs):
            return SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2)

    class QuotaService:
        async def reserve_run(self, **kwargs):
            return object(), ()

    class CostEstimator:
        async def estimate_micros(self, **kwargs):
            return 1

    class AuditSink:
        async def record(self, **kwargs):
            return None

    token_estimator = Estimator()
    quota_service = QuotaService()
    quota_cost_estimator = CostEstimator()
    audit_sink = AuditSink()

    async def complete(self, **kwargs):
        return kwargs


class RegistryRuntimeAssemblyTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_assembles_only_pinned_tools_and_reviewed_skills(self):
        async def trusted_handler(arguments):
            return {"ok": True}

        async def authorize(_context, _arguments):
            return True
        trusted = ToolDefinition(
            name="search", risk=RiskLevel.READ_ONLY, handler=trusted_handler,
            description="Trusted search",
            resource_resolver=lambda arguments: f"search:{arguments.get('query', 'tenant')}",
            resource_authorizer=authorize,
        )
        snapshot = SimpleNamespace(
            tenant_id="tenant-a", run_id="run-a", digest="d" * 64,
            entries=(
                {"kind": "model_template", "item_id": "general", "version": 1,
                 "definition": {"provider_id": "provider", "provider_version": 1,
                                "model_id": "model", "parameters": {},
                                "capabilities": ["text"], "allowed_data_levels": ["internal"]}},
                {"kind": "model_provider", "item_id": "provider", "version": 1,
                 "definition": {"provider_type": "openai-compatible",
                                "base_url": "https://llm.example/v1",
                                "secret_ref": "secret://tenant/provider"}},
                {"kind": "tool", "item_id": "search", "version": 2,
                 "definition": {"risk_level": "read_only", "input_schema": {
                     "type": "object", "properties": {}, "additionalProperties": False,
                 }, "allowed_roles": []}},
                {"kind": "skill", "item_id": "summarize", "version": 4,
                 "definition": {"manifest": {"name": "summarize", "dependencies": {}}}},
            ),
        )
        skill_item = RegistryVersion(
            tenant_id="tenant-a", kind="skill", item_id="summarize", version=4,
            definition={"manifest": {"name": "summarize", "dependencies": {}}},
            status="published", created_by="admin", created_at=datetime.now(timezone.utc),
        )
        reviewer = SkillDependencyReviewer(
            store=InMemorySkillDependencyReviewStore(), approved_pins={},
        )
        await reviewer.review(item=skill_item, actor_id="security-admin")
        assembler = RegistryRuntimeAssembler(
            snapshots=SnapshotService(snapshot), model_router=ModelRouter(),
            skill_reviewer=reviewer, trusted_tool_handlers={"search": trusted},
        )
        runtime = await assembler.assemble(
            tenant_id="tenant-a", run_id="run-a",
            model_template_id="general", required_tools=("search",),
        )
        self.assertEqual(runtime.snapshot_digest, "d" * 64)
        self.assertEqual([tool.name for tool in runtime.tools], ["search"])
        self.assertEqual(runtime.skill_reviews[0].status, "approved")
        routed = await runtime.model.complete(
            messages=({"role": "user", "content": "hello"},), request_id="req",
        )
        self.assertEqual(routed["template_id"], "general")
        self.assertEqual(routed["run_id"], "run-a")


    def test_runtime_assembly_rejects_model_router_without_budget_enforcement(self):
        class UnbudgetedModelRouter:
            async def complete(self, **kwargs):
                return kwargs

        with self.assertRaisesRegex(RegistryRuntimeError, "budget enforcement"):
            RegistryRuntimeAssembler(
                snapshots=SnapshotService(SimpleNamespace()),
                model_router=UnbudgetedModelRouter(),
                skill_reviewer=SkillDependencyReviewer(
                    store=InMemorySkillDependencyReviewStore(), approved_pins={},
                ),
                trusted_tool_handlers={},
            )

    def test_runtime_assembly_rejects_non_callable_budget_dependencies(self):
        class BrokenBudgetRouter(ModelRouter):
            quota_service = object()

        with self.assertRaisesRegex(RegistryRuntimeError, "quota_service.reserve_run"):
            RegistryRuntimeAssembler(
                snapshots=SnapshotService(SimpleNamespace()),
                model_router=BrokenBudgetRouter(),
                skill_reviewer=SkillDependencyReviewer(
                    store=InMemorySkillDependencyReviewStore(), approved_pins={},
                ),
                trusted_tool_handlers={},
            )

    def test_runtime_assembly_rejects_non_callable_token_estimator(self):
        class BrokenEstimatorRouter(ModelRouter):
            token_estimator = object()

        with self.assertRaisesRegex(RegistryRuntimeError, "token_estimator.estimate"):
            RegistryRuntimeAssembler(
                snapshots=SnapshotService(SimpleNamespace()),
                model_router=BrokenEstimatorRouter(),
                skill_reviewer=SkillDependencyReviewer(
                    store=InMemorySkillDependencyReviewStore(), approved_pins={},
                ),
                trusted_tool_handlers={},
            )

    async def test_unreviewed_skill_blocks_runtime_assembly(self):
        snapshot = SimpleNamespace(
            tenant_id="tenant-a", run_id="run-a", digest="e" * 64,
            entries=(
                {"kind": "model_template", "item_id": "general", "version": 1,
                 "definition": {}},
                {"kind": "skill", "item_id": "unsafe", "version": 1,
                 "definition": {"manifest": {"name": "unsafe", "dependencies": {}}}},
            ),
        )
        assembler = RegistryRuntimeAssembler(
            snapshots=SnapshotService(snapshot), model_router=ModelRouter(),
            skill_reviewer=SkillDependencyReviewer(
                store=InMemorySkillDependencyReviewStore(), approved_pins={},
            ),
            trusted_tool_handlers={},
        )
        with self.assertRaisesRegex(Exception, "not approved"):
            await assembler.assemble(
                tenant_id="tenant-a", run_id="run-a", model_template_id="general",
            )


if __name__ == "__main__":
    unittest.main()
