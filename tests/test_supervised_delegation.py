from __future__ import annotations

import asyncio
import unittest

from agent_platform.contracts import ModelTemplateRef, RunContext
from agent_platform.runtime.delegation import (
    DelegationPolicy, DelegationSpec, SupervisedDelegationService,
)
from agent_platform.runtime.protocols import HarnessResult
from agent_platform.workflows.expert_registry import ExpertProfile, InMemoryExpertRegistry


class FakeChildExecutor:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or HarnessResult(output_text="child result", usage={"total_tokens": 12})

    async def execute_child(self, *, request, parent_context, idempotency_key):
        self.calls.append((request, parent_context, idempotency_key))
        return self.result


class SupervisedDelegationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.parent = RunContext(
            tenant_id="tenant-a", run_id="parent-run", agent_id="orchestrator",
            model_template=ModelTemplateRef("general", 2), task_id="task-a",
            project_id="project-a", correlation_id="trace-a",
        )
        self.executor = FakeChildExecutor()
        self.registry = InMemoryExpertRegistry((
            ExpertProfile("researcher", "Researcher", "Research", frozenset({"research"})),
            ExpertProfile("critic", "Critic", "Review", frozenset({"review"})),
            ExpertProfile("disabled", "Disabled", "Disabled", frozenset({"research"}), enabled=False),
        ))
        self.service = SupervisedDelegationService(
            executor=self.executor,
            expert_registry=self.registry,
            policy=DelegationPolicy(
                allowed_agent_ids=frozenset({"researcher", "critic", "draft", "disabled"}),
                allowed_tool_ids=frozenset({"knowledge.search"}),
                max_depth=2, max_children=2, max_timeout_seconds=30,
                max_output_chars=100,
            ),
        )

    async def test_child_run_inherits_tenant_template_and_parent_trace(self):
        result = await self.service.delegate(parent=self.parent, requests=(
            DelegationSpec("research-1", "researcher", "Collect evidence",
                           allowed_tools=("knowledge.search",)),
        ))
        request, parent, idempotency_key = self.executor.calls[0]
        self.assertEqual(request.context.tenant_id, "tenant-a")
        self.assertEqual(request.context.agent_id, "researcher")
        self.assertEqual(request.context.model_template, self.parent.model_template)
        self.assertEqual(request.context.project_id, "project-a")
        self.assertEqual(request.context.correlation_id, "trace-a")
        self.assertEqual(request.context.metadata["parent_run_id"], "parent-run")
        self.assertEqual(request.allowed_tools, ("knowledge.search",))
        self.assertEqual(parent, self.parent)
        self.assertEqual(idempotency_key, "tenant-a:parent-run:research-1")
        self.assertEqual(result[0].child_run_id, request.context.run_id)

    async def test_child_run_id_is_stable_for_idempotent_retry(self):
        spec = DelegationSpec("same-key", "researcher", "Collect evidence")
        first = await self.service.delegate(parent=self.parent, requests=(spec,))
        second = await self.service.delegate(parent=self.parent, requests=(spec,))
        self.assertEqual(first[0].child_run_id, second[0].child_run_id)
        self.assertEqual(self.executor.calls[0][2], self.executor.calls[1][2])

    async def test_rejects_unregistered_agents_tools_cycles_and_depth(self):
        with self.assertRaises(PermissionError):
            await self.service.delegate(parent=self.parent, requests=(
                DelegationSpec("bad-agent", "unknown", "Do work"),
            ))
        with self.assertRaises(PermissionError):
            await self.service.delegate(parent=self.parent, requests=(
                DelegationSpec("draft-agent", "draft", "Do work"),
            ))
        with self.assertRaises(PermissionError):
            await self.service.delegate(parent=self.parent, requests=(
                DelegationSpec("disabled-agent", "disabled", "Do work"),
            ))
        with self.assertRaises(PermissionError):
            await self.service.delegate(parent=self.parent, requests=(
                DelegationSpec("missing-capability", "researcher", "Do work",
                               required_capabilities=("legal_review",)),
            ))
        with self.assertRaises(PermissionError):
            await self.service.delegate(parent=self.parent, requests=(
                DelegationSpec("bad-tool", "researcher", "Do work",
                               allowed_tools=("admin.delete",)),
            ))
        with self.assertRaises(PermissionError):
            await self.service.delegate(parent=self.parent, requests=(
                DelegationSpec("cycle", "orchestrator", "Call myself"),
            ))
        deep_parent = RunContext(
            tenant_id="tenant-a", run_id="deep", agent_id="orchestrator",
            model_template=ModelTemplateRef("general", 2),
            metadata={"delegation_depth": 2, "ancestor_agent_ids": ["orchestrator"]},
        )
        with self.assertRaises(PermissionError):
            await self.service.delegate(parent=deep_parent, requests=(
                DelegationSpec("too-deep", "researcher", "Do work"),
            ))

    async def test_rejects_registry_bound_to_another_tenant(self):
        class WrongTenantRegistry:
            tenant_id = "tenant-b"
            async def get(self, expert_id):
                return ExpertProfile(expert_id, expert_id, "", frozenset())

        self.service.expert_registry = WrongTenantRegistry()
        with self.assertRaises(PermissionError):
            await self.service.delegate(parent=self.parent, requests=(
                DelegationSpec("wrong-tenant", "researcher", "Do work"),
            ))

    async def test_rejects_excess_fanout_and_duplicate_keys(self):
        with self.assertRaises(ValueError):
            await self.service.delegate(parent=self.parent, requests=(
                DelegationSpec("a", "researcher", "A"),
                DelegationSpec("b", "critic", "B"),
                DelegationSpec("c", "researcher", "C"),
            ))
        with self.assertRaises(ValueError):
            await self.service.delegate(parent=self.parent, requests=(
                DelegationSpec("same", "researcher", "A"),
                DelegationSpec("same", "critic", "B"),
            ))

    async def test_timeout_and_output_limits_are_enforced(self):
        async def slow(**kwargs):
            await asyncio.sleep(0.05)
            return HarnessResult(output_text="late")
        self.executor.execute_child = slow
        with self.assertRaises(asyncio.TimeoutError):
            await self.service.delegate(parent=self.parent, requests=(
                DelegationSpec("timeout", "researcher", "Work", timeout_seconds=0.01),
            ))
        self.executor.result = HarnessResult(output_text="x" * 101)
        self.executor.execute_child = FakeChildExecutor(self.executor.result).execute_child
        with self.assertRaises(ValueError):
            await self.service.delegate(parent=self.parent, requests=(
                DelegationSpec("large", "researcher", "Work"),
            ))


if __name__ == "__main__":
    unittest.main()
