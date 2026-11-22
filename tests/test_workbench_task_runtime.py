from __future__ import annotations

from contextlib import asynccontextmanager
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from agent_platform.control_plane.registry import InMemoryRegistryRepository, RegistryService
from agent_platform.runtime.lifecycle import InMemoryRunStateStore, RunLifecycleManager, RunStatus
from agent_platform.runtime.registry_snapshot import InMemoryRunSnapshotStore, RunRegistrySnapshotService
from agent_platform.runtime.execution import LangGraphRunExecutor
from agent_platform.runtime.workbench_tasks import (
    RegistryBackedTaskCenterService,
    RegistryLangGraphExecutorResolver,
    RunExecutionWorker,
    WorkbenchTaskError,
)


class TestConcurrencyLimiter:
    """No-op shared-slot test double; production uses the Redis-backed limiter."""

    @asynccontextmanager
    async def slot(self, **_kwargs):
        yield object()


class TaskRunStore(InMemoryRunStateStore):
    def __init__(self):
        super().__init__()
        self.records = {}
        self.idempotency = {}

    async def create_task_run(
        self, *, tenant_id, run_id, actor_id, agent_id, agent_version, template_id,
        template_version, input, preparation_owner, project_id=None, session_id=None,
        workflow_id=None, idempotency_key=None, request_digest=None, registry_snapshot=None,
        priority=100,
    ):
        key = (tenant_id, actor_id, idempotency_key) if idempotency_key else None
        if key and key in self.idempotency:
            existing = self.records[self.idempotency[key]]
            if existing["request_digest"] != request_digest:
                raise ValueError("idempotency_key was already used with a different task request")
            return False
        await self.create(tenant_id, run_id)
        now = datetime.now(timezone.utc)
        self.records[run_id] = {
            "tenant_id": tenant_id, "run_id": run_id, "task_id": run_id,
            "project_id": project_id, "session_id": session_id, "agent_id": agent_id,
            "agent_version": agent_version, "template_id": template_id, "template_version": template_version,
            "workflow_id": workflow_id, "status": "queued", "priority": priority,
            "input": dict(input),
            "output": None, "usage": {}, "trace_id": None, "created_by": actor_id,
            "request_digest": request_digest, "registry_snapshot": list(registry_snapshot or []),
            "preparation_owner": preparation_owner,
            "created_at": now, "updated_at": now,
            "started_at": None, "finished_at": None, "checkpoint_ref": None,
            "last_error": None, "registry_snapshot_digest": None, "dispatch_ready": False,
        }
        if key:
            self.idempotency[key] = run_id
        return True

    async def create_retry_task_run(
        self, *, tenant_id, task_id, actor_id, run_id, idempotency_key, request_digest,
    ):
        key = (tenant_id, actor_id, idempotency_key)
        if key in self.idempotency:
            existing = self.records[self.idempotency[key]]
            if existing["request_digest"] != request_digest:
                raise ValueError("idempotency_key was already used with a different retry request")
            return existing["run_id"]
        source = self.records.get(task_id)
        if source is None or source["tenant_id"] != tenant_id or source["created_by"] != actor_id:
            raise KeyError("task not found")
        snapshot = await self.get(tenant_id, task_id)
        if snapshot is None or snapshot.status.value not in {"failed", "timed_out"}:
            raise ValueError("only failed or timed_out tasks can be retried")
        if not source.get("registry_snapshot_digest"):
            raise ValueError("task has no immutable registry snapshot and cannot be retried")
        await self.create(tenant_id, run_id)
        now = datetime.now(timezone.utc)
        self.records[run_id] = {
            **source, "run_id": run_id, "task_id": run_id, "status": "queued",
            "retry_of_run_id": task_id, "request_digest": request_digest,
            "idempotency_key": idempotency_key, "output": None, "usage": {},
            "trace_id": None, "started_at": None, "finished_at": None,
            "created_at": now, "updated_at": now, "checkpoint_ref": None,
            "last_error": None, "dispatch_ready": True, "preparation_owner": None,
        }
        self.idempotency[key] = run_id
        return run_id

    async def get_run_record(self, tenant_id, run_id):
        row = self.records.get(run_id)
        if row is None or row["tenant_id"] != tenant_id:
            return None
        return row

    async def get_task_record(self, tenant_id, task_id, *, actor_id=None):
        row = self.records.get(task_id)
        if row is None or row["tenant_id"] != tenant_id:
            return None
        if actor_id is not None and row["created_by"] != actor_id:
            return None
        snapshot = await self.get(tenant_id, task_id)
        return {**row, "status": snapshot.status.value, "checkpoint_ref": snapshot.checkpoint_ref,
                "last_error": snapshot.last_error}

    async def get_task_by_idempotency(self, tenant_id, actor_id, idempotency_key):
        run_id = self.idempotency.get((tenant_id, actor_id, idempotency_key))
        return await self.get_task_record(tenant_id, run_id, actor_id=actor_id) if run_id else None

    async def list_task_records(self, tenant_id, actor_id, *, limit=100):
        rows = []
        for run_id, row in self.records.items():
            if row["tenant_id"] == tenant_id and row["created_by"] == actor_id:
                rows.append(await self.get_task_record(tenant_id, run_id, actor_id=actor_id))
        return rows[:limit]

    async def claim_task_preparation(self, tenant_id, run_id, owner, *, lease_seconds=60):
        row = self.records.get(run_id)
        if row is None or row["tenant_id"] != tenant_id or row["dispatch_ready"]:
            return False
        if row.get("preparation_owner"):
            return False
        row["preparation_owner"] = owner
        return True

    async def owns_task_preparation(self, tenant_id, run_id, owner):
        row = self.records.get(run_id)
        return bool(row and row["tenant_id"] == tenant_id
                    and row.get("preparation_owner") == owner and not row["dispatch_ready"])

    async def mark_task_ready(self, tenant_id, run_id, digest, owner):
        row = self.records.get(run_id)
        if row is None or row["tenant_id"] != tenant_id or row.get("preparation_owner") != owner:
            return False
        row["registry_snapshot_digest"] = digest
        row["dispatch_ready"] = True
        row["preparation_owner"] = None
        return True

    async def save_output(self, tenant_id, run_id, output, *, usage=None, trace_id=None):
        self.records[run_id]["output"] = output
        self.records[run_id]["usage"] = dict(usage or {})
        self.records[run_id]["trace_id"] = trace_id

    async def claim_workbench_tasks(self, worker_id, *, tenant_id=None, limit=20, lease_seconds=60):
        rows = []
        ordered = sorted(
            self.records.items(),
            key=lambda item: (-item[1].get("priority", 100), item[1]["created_at"]),
        )
        for run_id, row in ordered:
            current = await self.get(row["tenant_id"], run_id)
            if (
                (tenant_id is None or row["tenant_id"] == tenant_id)
                and row.get("dispatch_ready") and current and current.status in {
                    RunStatus.QUEUED, RunStatus.INTERRUPTED, RunStatus.CANCELLING,
                }
            ):
                rows.append(current)
            if len(rows) >= limit:
                break
        return rows

    async def release_lease(self, tenant_id, run_id, worker_id):
        return True


class FakeGraph:
    def __init__(self):
        self.checkpointer = object()
        self.checkpoints = {}

    async def ainvoke(self, payload, *, config):
        thread_id = config["configurable"]["thread_id"]
        self.checkpoints[thread_id] = "cp-" + thread_id
        return {"answer": payload["text"] + " processed"}

    async def aget_state(self, config):
        checkpoint = self.checkpoints.get(config["configurable"]["thread_id"])
        return SimpleNamespace(config={"configurable": {"checkpoint_id": checkpoint}})


class WorkbenchTaskRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repository = InMemoryRegistryRepository()
        self.registry = RegistryService(self.repository)
        provider = await self.registry.create_draft(
            tenant_id="tenant-a", kind="model_provider", item_id="provider-a",
            definition={
                "provider_type": "openai-compatible",
                "base_url": "https://model.example/v1",
                "secret_ref": "secret://tenant-a/model-key",
            }, actor_id="admin",
        )
        provider = await self.registry.publish(
            tenant_id="tenant-a", kind="model_provider",
            item_id="provider-a", version=provider.version,
        )
        template = await self.registry.create_draft(
            tenant_id="tenant-a", kind="model_template", item_id="general",
            definition={
                "provider_id": "provider-a", "provider_version": provider.version,
                "model_id": "test-model", "parameters": {},
                "capabilities": ["text"], "allowed_data_levels": ["internal"],
            }, actor_id="admin",
        )
        await self.registry.publish(
            tenant_id="tenant-a", kind="model_template",
            item_id="general", version=template.version,
        )
        tool = await self.registry.create_draft(
            tenant_id="tenant-a", kind="tool", item_id="search",
            definition={"risk_level": "read_only"}, actor_id="admin",
        )
        await self.registry.publish(
            tenant_id="tenant-a", kind="tool", item_id="search", version=tool.version,
        )
        skill = await self.registry.create_draft(
            tenant_id="tenant-a", kind="skill", item_id="summarize",
            definition={"manifest": {"name": "summarize", "dependencies": {}}},
            actor_id="admin",
        )
        await self.registry.publish(
            tenant_id="tenant-a", kind="skill", item_id="summarize", version=skill.version,
        )
        agent = await self.registry.create_draft(
            tenant_id="tenant-a", kind="agent", item_id="researcher",
            definition={
                "instructions": "Research the supplied question.",
                "model_template_id": "general",
                "tool_ids": ["search"], "skill_ids": ["summarize"],
            }, actor_id="admin",
        )
        await self.registry.publish(
            tenant_id="tenant-a", kind="agent", item_id="researcher", version=agent.version,
        )
        self.snapshots = RunRegistrySnapshotService(
            registry=self.registry, store=InMemoryRunSnapshotStore(),
        )
        self.run_store = TaskRunStore()
        self.lifecycle = RunLifecycleManager(self.run_store)
        self.service = RegistryBackedTaskCenterService(
            tenant_id="tenant-a", registry=self.registry,
            snapshots=self.snapshots, run_store=self.run_store,
            lifecycle=self.lifecycle,
        )

    async def test_submit_pins_agent_model_provider_tool_and_skill_versions(self):
        task = await self.service.submit_task(
            tenant_id="tenant-a", actor_id="user-a", agent_id="researcher",
            project_id="project-a", session_id="session-a",
            input={"text": "market analysis"},
        )
        self.assertEqual(task["status"], "queued")
        self.assertTrue(task["dispatch_ready"])
        self.assertEqual(task["project_id"], "project-a")
        self.assertEqual(task["session_id"], "session-a")
        snapshot = await self.snapshots.get(
            tenant_id="tenant-a", run_id=task["run_id"],
        )
        refs = {(entry["kind"], entry["item_id"]) for entry in snapshot.entries}
        self.assertEqual(refs, {
            ("agent", "researcher"), ("model_template", "general"),
            ("model_provider", "provider-a"), ("tool", "search"),
            ("skill", "summarize"),
        })

    async def test_failed_task_retry_creates_new_run_with_same_pinned_snapshot(self):
        original = await self.service.submit_task(
            tenant_id="tenant-a", actor_id="user-a", agent_id="researcher",
            project_id=None, session_id=None, input={"text": "retry me"},
            priority=700,
        )
        original_row = self.run_store.records[original["run_id"]]
        original_row["registry_snapshot_digest"] = "a" * 64
        original_row["dispatch_ready"] = True
        await self.lifecycle.finish(
            "tenant-a", original["run_id"], RunStatus.FAILED, error="provider unavailable",
        )
        retried = await self.service.retry_task(
            tenant_id="tenant-a", task_id=original["task_id"], actor_id="user-a",
            idempotency_key="retry-1",
        )
        self.assertEqual(retried["status"], "queued")
        self.assertEqual(retried["priority"], 700)
        self.assertEqual(retried["retry_of_run_id"], original["run_id"])
        self.assertEqual(retried["input"], original["input"])
        self.assertEqual(
            self.run_store.records[retried["run_id"]]["registry_snapshot"],
            original_row["registry_snapshot"],
        )
        same_retry = await self.service.retry_task(
            tenant_id="tenant-a", task_id=original["task_id"], actor_id="user-a",
            idempotency_key="retry-1",
        )
        self.assertEqual(same_retry["run_id"], retried["run_id"])

    async def test_task_priority_is_persisted_and_bound_to_idempotency(self):
        normal = await self.service.submit_task(
            tenant_id="tenant-a", actor_id="user-a", agent_id="researcher",
            project_id=None, session_id=None, input={"text": "normal"},
            idempotency_key="priority-normal",
        )
        urgent = await self.service.submit_task(
            tenant_id="tenant-a", actor_id="user-a", agent_id="researcher",
            project_id=None, session_id=None, input={"text": "urgent"},
            idempotency_key="priority-urgent", priority=900,
        )
        self.assertEqual(normal["priority"], 100)
        self.assertEqual(urgent["priority"], 900)
        with self.assertRaisesRegex(WorkbenchTaskError, "different task request"):
            await self.service.submit_task(
                tenant_id="tenant-a", actor_id="user-a", agent_id="researcher",
                project_id=None, session_id=None, input={"text": "urgent"},
                idempotency_key="priority-urgent", priority=100,
            )
        claimed = await self.run_store.claim_workbench_tasks("worker-priority", limit=2)
        self.assertEqual([row.run_id for row in claimed], [urgent["run_id"], normal["run_id"]])

    async def test_idempotency_returns_original_task_and_rejects_payload_reuse(self):
        first = await self.service.submit_task(
            tenant_id="tenant-a", actor_id="user-a", agent_id="researcher",
            project_id=None, session_id=None, input={"text": "same"},
            idempotency_key="request-1",
        )
        replay = await self.service.submit_task(
            tenant_id="tenant-a", actor_id="user-a", agent_id="researcher",
            project_id=None, session_id=None, input={"text": "same"},
            idempotency_key="request-1",
        )
        self.assertEqual(first["task_id"], replay["task_id"])
        with self.assertRaisesRegex(ValueError, "different task request"):
            await self.service.submit_task(
                tenant_id="tenant-a", actor_id="user-a", agent_id="researcher",
                project_id=None, session_id=None, input={"text": "changed"},
                idempotency_key="request-1",
            )

    async def test_task_detail_and_list_are_actor_scoped(self):
        task = await self.service.submit_task(
            tenant_id="tenant-a", actor_id="user-a", agent_id="researcher",
            project_id=None, session_id=None, input={"text": "private"},
        )
        self.assertIsNotNone(await self.service.get_task(
            tenant_id="tenant-a", task_id=task["task_id"], actor_id="user-a",
        ))
        self.assertIsNone(await self.service.get_task(
            tenant_id="tenant-a", task_id=task["task_id"], actor_id="user-b",
        ))
        self.assertEqual(len(await self.service.list_tasks(
            tenant_id="tenant-a", actor_id="user-b",
        )), 0)

    async def test_executor_resolver_uses_run_pinned_agent_definition(self):
        task = await self.service.submit_task(
            tenant_id="tenant-a", actor_id="user-a", agent_id="researcher",
            project_id=None, session_id=None, input={"text": "resolve"},
        )
        record = await self.run_store.get_task_record(
            "tenant-a", task["task_id"], actor_id="user-a",
        )
        graph = FakeGraph()
        runtime_provider = SimpleNamespace(
            prepare=lambda **kwargs: None,
            release=lambda tenant_id, run_id: None,
        )
        seen = {}

        async def graph_factory(**kwargs):
            seen.update(kwargs)
            return graph

        async def lifecycle_resolver(tenant_id):
            return self.lifecycle

        async def runtime_provider_resolver(tenant_id):
            return runtime_provider

        resolver = RegistryLangGraphExecutorResolver(
            snapshots=self.snapshots,
            lifecycle_resolver=lifecycle_resolver,
            runtime_provider_resolver=runtime_provider_resolver,
            graph_factory=graph_factory,
        )
        executor = await resolver(record, "worker-pinned")
        self.assertEqual(executor.worker_id, "worker-pinned")
        self.assertEqual(seen["agent_definition"]["model_template_id"], "general")
        self.assertEqual(seen["agent_definition"]["tool_ids"], ["search"])
        self.assertEqual(seen["snapshot"].digest, (
            await self.snapshots.get(tenant_id="tenant-a", run_id=task["run_id"])
        ).digest)

    async def test_worker_executes_queued_task_and_persists_output(self):
        task = await self.service.submit_task(
            tenant_id="tenant-a", actor_id="user-a", agent_id="researcher",
            project_id=None, session_id=None, input={"text": "research"},
        )
        graph = FakeGraph()

        async def executor_resolver(record, worker_id):
            return LangGraphRunExecutor(
                graph=graph, lifecycle=self.lifecycle, worker_id=worker_id,
                timeout_seconds=2,
            )

        async def lifecycle_resolver(tenant_id):
            return self.lifecycle

        worker = RunExecutionWorker(
            run_store=self.run_store, executor_resolver=executor_resolver,
            lifecycle_resolver=lifecycle_resolver, worker_id="worker-a",
            concurrency_limiter=TestConcurrencyLimiter(), worker_pool_scope_id="test",
            queue_scope_resolver=lambda record: "interactive",
        )
        result = await worker.run_once()
        self.assertEqual(result["succeeded"], 1)
        updated = await self.service.get_task(
            tenant_id="tenant-a", task_id=task["task_id"], actor_id="user-a",
        )
        self.assertEqual(updated["status"], "succeeded")
        self.assertEqual(updated["output"], {"answer": "research processed"})


    async def test_submit_accepts_stable_child_run_id_and_binds_it_to_idempotency(self):
        child_run_id = "00000000-0000-0000-0000-000000000123"
        task = await self.service.submit_task(
            tenant_id="tenant-a",
            actor_id="user-a",
            agent_id="researcher",
            project_id="project-a",
            session_id=None,
            input={"input_text": "delegated task"},
            idempotency_key="child-run-stable-key",
            run_id=child_run_id,
        )
        self.assertEqual(task["run_id"], child_run_id)
        self.assertEqual(task["task_id"], child_run_id)
        self.assertEqual(task["template_id"], "general")
        self.assertEqual(task["template_version"], 2)

        with self.assertRaisesRegex(WorkbenchTaskError, "run_id must be a UUID"):
            await self.service.submit_task(
                tenant_id="tenant-a",
                actor_id="user-a",
                agent_id="researcher",
                project_id="project-a",
                session_id=None,
                input={"input_text": "invalid"},
                run_id="not-a-uuid",
            )

    async def test_worker_keeps_async_executor_context_open_through_run(self):
        task = await self.service.submit_task(
            tenant_id="tenant-a", actor_id="user-a", agent_id="researcher",
            project_id=None, session_id=None, input={"text": "scoped"},
        )
        graph = FakeGraph()
        events = []

        @asynccontextmanager
        async def executor_resolver(_record, worker_id):
            events.append("enter")
            yield LangGraphRunExecutor(
                graph=graph, lifecycle=self.lifecycle, worker_id=worker_id,
                timeout_seconds=2,
            )
            events.append("exit")

        async def lifecycle_resolver(_tenant_id):
            return self.lifecycle

        worker = RunExecutionWorker(
            run_store=self.run_store, executor_resolver=executor_resolver,
            lifecycle_resolver=lifecycle_resolver, worker_id="worker-scoped",
            concurrency_limiter=TestConcurrencyLimiter(), worker_pool_scope_id="test",
            queue_scope_resolver=lambda record: "interactive",
        )
        result = await worker.run_once()
        self.assertEqual(result["succeeded"], 1)
        self.assertEqual(events, ["enter", "exit"])
        updated = await self.service.get_task(
            tenant_id="tenant-a", task_id=task["task_id"], actor_id="user-a",
        )
        self.assertEqual(updated["status"], "succeeded")




    async def test_worker_enforces_shared_queue_and_worker_pool_slots(self):
        from contextlib import asynccontextmanager

        task = await self.service.submit_task(
            tenant_id="tenant-a", actor_id="user-a", agent_id="researcher",
            project_id=None, session_id=None, input={"text": "limited"},
        )
        events = []

        class Limiter:
            @asynccontextmanager
            async def slot(self, *, tenant_id, run_id, scope_type, scope_id):
                events.append(("acquire", scope_type, scope_id))
                try:
                    yield object()
                finally:
                    events.append(("release", scope_type, scope_id))

        graph = FakeGraph()
        async def executor_resolver(_record, worker_id):
            return LangGraphRunExecutor(
                graph=graph, lifecycle=self.lifecycle, worker_id=worker_id,
                timeout_seconds=2,
            )
        async def lifecycle_resolver(_tenant_id):
            return self.lifecycle

        worker = RunExecutionWorker(
            run_store=self.run_store, executor_resolver=executor_resolver,
            lifecycle_resolver=lifecycle_resolver, worker_id="worker-limited",
            concurrency_limiter=Limiter(), worker_pool_scope_id="gpu",
            queue_scope_resolver=lambda record: "interactive",
        )
        result = await worker.run_once()
        self.assertEqual(result["succeeded"], 1)
        self.assertEqual(events, [
            ("acquire", "queue", "interactive"),
            ("acquire", "worker_pool", "gpu"),
            ("release", "worker_pool", "gpu"),
            ("release", "queue", "interactive"),
        ])
        updated = await self.service.get_task(
            tenant_id="tenant-a", task_id=task["task_id"], actor_id="user-a",
        )
        self.assertEqual(updated["status"], "succeeded")

    async def test_worker_keeps_task_queued_when_shared_concurrency_is_exhausted(self):
        from agent_platform.quotas.service import BudgetExceeded

        task = await self.service.submit_task(
            tenant_id="tenant-a", actor_id="user-a", agent_id="researcher",
            project_id=None, session_id=None, input={"text": "wait"},
        )
        class FullLimiter:
            @asynccontextmanager
            async def slot(self, **_kwargs):
                raise BudgetExceeded("queue full")
                yield
        graph = FakeGraph()
        async def executor_resolver(_record, worker_id):
            return LangGraphRunExecutor(
                graph=graph, lifecycle=self.lifecycle, worker_id=worker_id,
                timeout_seconds=2,
            )
        async def lifecycle_resolver(_tenant_id):
            return self.lifecycle
        worker = RunExecutionWorker(
            run_store=self.run_store, executor_resolver=executor_resolver,
            lifecycle_resolver=lifecycle_resolver, worker_id="worker-full",
            concurrency_limiter=FullLimiter(), worker_pool_scope_id="gpu",
            queue_scope_resolver=lambda record: "interactive",
        )
        result = await worker.run_once()
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(graph.checkpoints, {})
        self.assertEqual((await self.run_store.get("tenant-a", task["run_id"])).status, RunStatus.QUEUED)

    async def test_registry_executor_resolver_preserves_graph_context(self):
        task = await self.service.submit_task(
            tenant_id="tenant-a", actor_id="user-a", agent_id="researcher",
            project_id=None, session_id=None, input={"text": "context"},
        )
        record = await self.run_store.get_task_record(
            "tenant-a", task["task_id"], actor_id="user-a",
        )
        graph = FakeGraph()
        events = []

        @asynccontextmanager
        async def graph_factory(**_kwargs):
            events.append("graph-open")
            yield graph
            events.append("graph-close")

        async def lifecycle_resolver(_tenant_id):
            return self.lifecycle

        runtime_provider = SimpleNamespace(
            prepare=lambda **kwargs: None,
            release=lambda tenant_id, run_id: None,
        )

        async def runtime_provider_resolver(_tenant_id):
            return runtime_provider

        resolver = RegistryLangGraphExecutorResolver(
            snapshots=self.snapshots,
            lifecycle_resolver=lifecycle_resolver,
            runtime_provider_resolver=runtime_provider_resolver,
            graph_factory=graph_factory,
        )
        scoped = await resolver(record, "worker-context")
        async with scoped as executor:
            self.assertEqual(executor.worker_id, "worker-context")
            self.assertEqual(events, ["graph-open"])
        self.assertEqual(events, ["graph-open", "graph-close"])



if __name__ == "__main__":
    unittest.main()


    async def test_preparation_retry_never_rebinds_to_new_active_agent_version(self):
        from agent_platform.runtime.workbench_tasks import WorkbenchTaskError

        task = await self.service.submit_task(
            tenant_id="tenant-a", actor_id="user-a", agent_id="researcher",
            project_id=None, session_id=None, input={"text": "pinned"},
            idempotency_key="replay-pinned",
        )
        row = self.run_store.records[task["run_id"]]
        pinned_version = row["agent_version"]
        self.assertEqual(
            next(ref["version"] for ref in row["registry_snapshot"]
                 if ref["kind"] == "agent"), pinned_version,
        )
        # Simulate a process crash after Run creation but before snapshot readiness.
        row["dispatch_ready"] = False
        row["registry_snapshot_digest"] = None
        self.snapshots.store._rows.pop(("tenant-a", task["run_id"]), None)

        draft = await self.registry.create_draft(
            tenant_id="tenant-a", kind="agent", item_id="researcher",
            definition={
                "instructions": "A newer definition must not replace the queued Run.",
                "model_template_id": "general",
                "tool_ids": ["search"], "skill_ids": ["summarize"],
            }, actor_id="admin",
        )
        await self.registry.publish(
            tenant_id="tenant-a", kind="agent", item_id="researcher",
            version=draft.version,
        )
        with self.assertRaises(WorkbenchTaskError):
            await self.service.submit_task(
                tenant_id="tenant-a", actor_id="user-a", agent_id="researcher",
                project_id=None, session_id=None, input={"text": "pinned"},
                idempotency_key="replay-pinned",
            )
        failed = await self.service.get_task(
            tenant_id="tenant-a", task_id=task["task_id"], actor_id="user-a",
        )
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(row["agent_version"], pinned_version)
