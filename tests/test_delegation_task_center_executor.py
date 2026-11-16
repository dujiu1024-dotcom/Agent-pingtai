from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from agent_platform.contracts import ModelTemplateRef, RunContext
from agent_platform.runtime.delegation_task_center import TaskCenterChildRunExecutor
from agent_platform.runtime.protocols import HarnessRequest


class FakeRegistry:
    def __init__(self, *, template_version=2, tools=("knowledge.search",)):
        self.template_version = template_version
        self.tools = list(tools)

    async def resolve_active(self, *, tenant_id, kind, item_id):
        if tenant_id != "tenant-a":
            raise PermissionError("wrong tenant")
        if kind == "agent":
            return SimpleNamespace(
                definition={"model_template_id": "general", "tool_ids": self.tools},
                version=4,
            )
        if kind == "model_template":
            return SimpleNamespace(version=self.template_version)
        raise AssertionError(kind)


class FakeTaskService:
    def __init__(self, *, template_version=2, tools=("knowledge.search",)):
        self.run_store = object()
        self.registry = FakeRegistry(template_version=template_version, tools=tools)
        self.row = None
        self.submitted = None
        self.cancelled = False

    @staticmethod
    def _string_list(value, field):
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(field)
        return tuple(value)

    async def submit_task(self, **kwargs):
        self.submitted = kwargs
        self.row = {
            "task_id": kwargs["run_id"],
            "run_id": kwargs["run_id"],
            "status": "queued",
            "output": None,
            "usage": {},
            "template_id": "general",
            "template_version": 2,
        }
        return {
            "task_id": kwargs["run_id"],
            "run_id": kwargs["run_id"],
            "template_id": "general",
            "template_version": 2,
        }

    async def get_task(self, **kwargs):
        return self.row

    async def cancel_task(self, **kwargs):
        self.cancelled = True
        if self.row is not None:
            self.row["status"] = "cancelled"
        return self.row


class FakeWorker:
    def __init__(self, task_service):
        self.run_store = task_service.run_store
        self.task_service = task_service
        self.calls = 0

    async def run_once(self):
        self.calls += 1
        self.task_service.row.update({
            "status": "succeeded",
            "output": {"text": "delegated answer"},
            "usage": {"total_tokens": 17},
            "trace_id": "child-trace",
        })
        return {"claimed": 1, "succeeded": 1}


class TaskCenterChildRunExecutorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.parent = RunContext(
            tenant_id="tenant-a",
            run_id="parent-run",
            agent_id="orchestrator",
            model_template=ModelTemplateRef("general", 2),
            task_id="parent-task",
            project_id="project-a",
            correlation_id="parent-trace",
            metadata={"session_id": "session-a"},
        )
        self.child = RunContext(
            tenant_id="tenant-a",
            run_id="00000000-0000-0000-0000-000000000123",
            agent_id="researcher",
            model_template=ModelTemplateRef("general", 2),
            task_id="parent-task",
            project_id="project-a",
            correlation_id="parent-trace",
            metadata={
                "parent_run_id": "parent-run",
                "delegation_key": "research-1",
                "delegation_depth": 1,
            },
        )
        self.request = HarnessRequest(
            context=self.child,
            instructions="Research the topic",
            input_text="Find reliable evidence",
            allowed_tools=("knowledge.search",),
            metadata={"delegation_key": "research-1"},
        )
        self.service = FakeTaskService()
        self.worker = FakeWorker(self.service)
        self.executor = TaskCenterChildRunExecutor(
            task_service=self.service,
            worker=self.worker,
            actor_resolver=lambda context: "user-a",
        )

    async def test_child_run_is_durable_idempotent_and_returns_normalized_result(self):
        result = await self.executor.execute_child(
            request=self.request,
            parent_context=self.parent,
            idempotency_key="tenant-a:parent-run:research-1",
        )
        self.assertEqual(self.service.submitted["run_id"], self.child.run_id)
        self.assertEqual(self.service.submitted["actor_id"], "user-a")
        self.assertEqual(self.service.submitted["tenant_id"], "tenant-a")
        self.assertEqual(self.service.submitted["idempotency_key"][:10], "child-run:")
        self.assertEqual(self.service.submitted["input"]["metadata"]["parent_run_id"], "parent-run")
        self.assertEqual(result.output_text, "delegated answer")
        self.assertEqual(result.usage["total_tokens"], 17)
        self.assertEqual(result.trace_id, "child-trace")
        self.assertEqual(result.metadata["child_run_id"], self.child.run_id)

    async def test_child_worker_can_be_bound_after_runtime_composition(self):
        executor = TaskCenterChildRunExecutor(
            task_service=self.service,
            actor_resolver=lambda context: "user-a",
        )
        executor.bind_worker(self.worker)
        result = await executor.execute_child(
            request=self.request,
            parent_context=self.parent,
            idempotency_key="tenant-a:parent-run:research-1",
        )
        self.assertEqual(result.output_text, "delegated answer")

    async def test_child_worker_must_share_the_durable_run_store(self):
        executor = TaskCenterChildRunExecutor(
            task_service=self.service,
            actor_resolver=lambda context: "user-a",
        )
        with self.assertRaisesRegex(ValueError, "share the durable RunStateStore"):
            executor.bind_worker(SimpleNamespace(run_store=object(), run_once=lambda: None))

    async def test_rejects_unapproved_tools_before_persisting_child(self):
        self.request = HarnessRequest(
            context=self.child,
            instructions="Do work",
            input_text="Input",
            allowed_tools=("admin.delete",),
        )
        with self.assertRaisesRegex(PermissionError, "tool allowlist"):
            await self.executor.execute_child(
                request=self.request,
                parent_context=self.parent,
                idempotency_key="stable-key",
            )
        self.assertIsNone(self.service.submitted)

    async def test_rejects_changed_model_template_version(self):
        self.service.registry.template_version = 3
        with self.assertRaisesRegex(PermissionError, "template version"):
            await self.executor.execute_child(
                request=self.request,
                parent_context=self.parent,
                idempotency_key="stable-key",
            )
        self.assertIsNone(self.service.submitted)

    async def test_worker_failure_cancels_orphaned_child_run(self):
        class FailingWorker(FakeWorker):
            async def run_once(self):
                raise RuntimeError("worker queue unavailable")

        self.executor.worker = FailingWorker(self.service)
        with self.assertRaisesRegex(RuntimeError, "worker queue unavailable"):
            await self.executor.execute_child(
                request=self.request,
                parent_context=self.parent,
                idempotency_key="stable-key",
            )
        self.assertTrue(self.service.cancelled)

    async def test_cancelling_parent_execution_cancels_durable_child(self):
        class CancellingWorker(FakeWorker):
            async def run_once(self):
                raise asyncio.CancelledError()

        self.executor.worker = CancellingWorker(self.service)
        with self.assertRaises(asyncio.CancelledError):
            await self.executor.execute_child(
                request=self.request,
                parent_context=self.parent,
                idempotency_key="stable-key",
            )
        self.assertTrue(self.service.cancelled)


if __name__ == "__main__":
    unittest.main()
