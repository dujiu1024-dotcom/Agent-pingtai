from __future__ import annotations

import unittest
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

from agent_platform.observability.run_trace import (
    InMemoryRunTraceRepository,
    LangfuseRunTraceExporter,
    PostgresRunTraceRepository,
    RunTraceCallbackHandler,
    RunTraceEvent,
    RunTraceEventType,
    RunTraceService,
)


class RunTraceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repository = InMemoryRunTraceRepository()
        self.service = RunTraceService(repository=self.repository)

    async def test_events_are_ordered_tenant_scoped_and_paginated(self):
        first = await self.service.emit(
            tenant_id="tenant-a", run_id="run-1",
            event_type=RunTraceEventType.RUN_START, payload={"worker_id": "worker-a"},
        )
        second = await self.service.emit(
            tenant_id="tenant-a", run_id="run-1",
            event_type=RunTraceEventType.STEP_START, step_id="node-1",
            payload={"name": "planner"},
        )
        await self.service.emit(
            tenant_id="tenant-b", run_id="run-1",
            event_type=RunTraceEventType.RUN_START,
        )

        self.assertEqual((first.sequence, second.sequence), (1, 2))
        self.assertEqual(second.step_id, "node-1")
        page = await self.repository.list_events("tenant-a", "run-1", limit=1)
        self.assertEqual([event.event_id for event in page], [first.event_id])
        tail = await self.repository.list_events("tenant-a", "run-1", after_sequence=1)
        self.assertEqual([event.event_type for event in tail], [RunTraceEventType.STEP_START])
        self.assertEqual(len(await self.repository.list_events("tenant-b", "run-1")), 1)

    async def test_payload_redaction_prevents_secrets_from_being_persisted(self):
        event = await self.service.emit(
            tenant_id="tenant-a", run_id="run-secret",
            event_type=RunTraceEventType.TOOL_CALL,
            payload={"api_key": "super-secret", "authorization": "Bearer abc.def.ghi"},
        )
        self.assertEqual(event.payload["api_key"], "[REDACTED]")
        self.assertIn("[REDACTED]", event.payload["authorization"])

    async def test_callback_handler_maps_step_llm_tool_and_error_events(self):
        handler = RunTraceCallbackHandler(
            trace=self.service, tenant_id="tenant-a", run_id="run-callback",
        )
        await handler.on_chain_start({"name": "planner"}, {"query": "private"}, run_id="step-1")
        await handler.on_chain_end({"answer": "private"}, run_id="step-1")
        await handler.on_llm_start({"name": "model-x"}, ["secret prompt"], run_id="llm-1")
        await handler.on_llm_end(SimpleNamespace(generations=[[object()]]), run_id="llm-1")
        await handler.on_tool_start({"name": "search"}, "secret input", run_id="tool-1", name="search")
        await handler.on_tool_end("secret output", run_id="tool-1")
        await handler.on_tool_error(RuntimeError("provider detail"), run_id="tool-2")
        events = await self.repository.list_events("tenant-a", "run-callback")
        self.assertEqual(
            [event.event_type for event in events],
            [
                RunTraceEventType.STEP_START, RunTraceEventType.STEP_FINISH,
                RunTraceEventType.LLM_REQUEST, RunTraceEventType.LLM_RESPONSE,
                RunTraceEventType.TOOL_CALL, RunTraceEventType.STEP_FINISH,
                RunTraceEventType.ERROR,
            ],
        )
        serialized = repr([event.payload for event in events])
        self.assertNotIn("secret prompt", serialized)
        self.assertNotIn("secret input", serialized)
        self.assertNotIn("secret output", serialized)
        self.assertNotIn("provider detail", serialized)

    async def test_blackboard_bridge_requires_run_scope_and_links_event_id(self):
        blackboard = SimpleNamespace(
            event_id="bb-event-1", event_type="blackboard.entry_proposed",
            scope=SimpleNamespace(value="run"), scope_id="run-bb",
            entry_id="entry-1", entry_version=2, actor_id="agent-a",
            correlation_id="corr-1",
        )
        result = await self.service.emit_blackboard_event(
            tenant_id="tenant-a", run_id="run-bb", event=blackboard,
        )
        self.assertEqual(result.event_type, RunTraceEventType.BLACKBOARD_EVENT)
        self.assertEqual(result.payload["blackboard_event_id"], "bb-event-1")
        blackboard.scope_id = "another-run"
        with self.assertRaisesRegex(ValueError, "scoped to the target Run"):
            await self.service.emit_blackboard_event(
                tenant_id="tenant-a", run_id="run-bb", event=blackboard,
            )

    async def test_blackboard_replay_import_is_idempotent_and_preserves_source_id(self):
        from datetime import datetime, timezone
        from agent_platform.contracts import Scope

        event = SimpleNamespace(
            event_id="bb-uuid", event_type="blackboard.entry_proposed",
            scope=Scope.RUN, scope_id="run-import", entry_id="entry-x",
            entry_version=1, actor_id="agent-a", correlation_id="corr-x",
            occurred_at=datetime.now(timezone.utc),
        )
        class Replay:
            async def list_after(self, *, cursor, scope, scope_id, limit):
                self.assertions = (scope, scope_id, limit)
                return [event] if cursor is None else []
            @staticmethod
            def cursor_for(item):
                return item.event_id

        replay = Replay()
        service = RunTraceService(repository=self.repository, blackboard_replay=replay)
        self.assertEqual(await service.capture_blackboard_events(tenant_id="tenant-a", run_id="run-import"), 1)
        self.assertEqual(await service.capture_blackboard_events(tenant_id="tenant-a", run_id="run-import"), 1)
        events = await self.repository.list_events("tenant-a", "run-import")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_id, "bb-uuid")
        self.assertEqual(events[0].occurred_at, event.occurred_at)

    async def test_executor_emits_run_start_step_and_complete(self):
        from types import SimpleNamespace
        from agent_platform.runtime.execution import LangGraphRunExecutor
        from agent_platform.runtime.lifecycle import InMemoryRunStateStore, RunLifecycleManager

        class Graph:
            checkpointer = object()
            async def ainvoke(self, payload, *, config):
                handler = config["callbacks"][0]
                await handler.on_chain_start({"name": "workflow"}, payload, run_id="step-root")
                await handler.on_chain_end({"result": "ok"}, run_id="step-root")
                return {"result": payload}
            async def aget_state(self, config):
                return SimpleNamespace(config={"configurable": {"checkpoint_id": "cp-final"}})

        store = InMemoryRunStateStore()
        await store.create("tenant-a", "run-exec")
        trace = RunTraceService(repository=self.repository)
        executor = LangGraphRunExecutor(
            graph=Graph(), lifecycle=RunLifecycleManager(store), trace_service=trace,
        )
        await executor.execute(tenant_id="tenant-a", run_id="run-exec", input={"prompt": "hi"})
        events = await self.repository.list_events("tenant-a", "run-exec")
        self.assertEqual(
            [event.event_type for event in events],
            [RunTraceEventType.RUN_START, RunTraceEventType.STEP_START,
             RunTraceEventType.STEP_FINISH, RunTraceEventType.COMPLETE],
        )

    async def test_langfuse_export_is_a_projection_of_persisted_events(self):
        class FakeObservation:
            def __init__(self):
                self.output = None
            def update(self, **kwargs):
                self.output = kwargs.get("output")

        class FakeSink:
            def __init__(self):
                self.observations = []
            @contextmanager
            def observe(self, *, name, input, metadata):
                observation = FakeObservation()
                self.observations.append((name, input, metadata, observation))
                yield observation

        sink = FakeSink()
        service = RunTraceService(
            repository=self.repository,
            exporter=LangfuseRunTraceExporter(sink),
        )
        stored = await service.emit(
            tenant_id="tenant-a", run_id="run-langfuse",
            event_type=RunTraceEventType.COMPLETE, payload={"status": "succeeded"},
        )
        self.assertEqual(sink.observations[0][0], "run.complete")
        self.assertEqual(sink.observations[0][2]["event_id"], stored.event_id)
        self.assertEqual(sink.observations[0][3].output["status"], "succeeded")


class _Cursor:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []
    async def fetchone(self):
        return self.row
    async def fetchall(self):
        return self.rows


class _FakeConnection:
    def __init__(self, db):
        self.db = db
        self.sql = []
    async def __aenter__(self):
        return self
    async def __aexit__(self, *_args):
        return None
    @asynccontextmanager
    async def transaction(self):
        yield self
    async def execute(self, query, params=()):
        self.sql.append(query)
        if "CREATE TABLE" in query or "CREATE INDEX" in query or "pg_advisory_xact_lock" in query:
            return _Cursor()
        if "SELECT sequence, event_id" in query:
            row = next((r for r in self.db if r["event_id"] == params[2]), None)
            return _Cursor(row=row)
        if "SELECT COALESCE(MAX(sequence)" in query:
            latest = max((r["sequence"] for r in self.db if r["tenant_id"] == params[0] and r["run_id"] == params[1]), default=0)
            return _Cursor(row={"latest": latest})
        if "INSERT INTO platform_run_trace_events" in query:
            tenant, run, seq, event_id, kind, at, step, parent, payload = params
            self.db.append({
                "tenant_id": tenant, "run_id": run, "sequence": seq,
                "event_id": event_id, "event_type": kind, "occurred_at": at,
                "step_id": step, "parent_event_id": parent,
                "payload": payload.obj,
            })
            return _Cursor()
        if "SELECT tenant_id, run_id, sequence, event_id" in query:
            tenant, run, after, limit = params
            rows = sorted(
                [r for r in self.db if r["tenant_id"] == tenant and r["run_id"] == run and r["sequence"] > after],
                key=lambda r: r["sequence"],
            )[:limit]
            return _Cursor(rows=rows)
        raise AssertionError(f"unexpected SQL: {query}")


class _Jsonb:
    def __init__(self, obj):
        self.obj = obj


class PostgresRunTraceRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import sys
        from unittest.mock import patch
        self.database = []
        self.connection = _FakeConnection(self.database)
        async def connect():
            return self.connection
        self.repository = PostgresRunTraceRepository(connection_factory=connect)
        psycopg = __import__("types").ModuleType("psycopg")
        psycopg.__path__ = []
        psycopg_types = __import__("types").ModuleType("psycopg.types")
        psycopg_types.__path__ = []
        psycopg_json = __import__("types").ModuleType("psycopg.types.json")
        psycopg_json.Jsonb = _Jsonb
        self.patch = patch.dict(sys.modules, {
            "psycopg": psycopg, "psycopg.types": psycopg_types,
            "psycopg.types.json": psycopg_json,
        })
        self.patch.start()
        self.addCleanup(self.patch.stop)

    async def test_schema_setup_and_append_only_tenant_run_timeline(self):
        await self.repository.ensure_schema()
        stored = await self.repository.append(RunTraceEvent(
            tenant_id="tenant-a", run_id="run-uuid",
            event_type=RunTraceEventType.RUN_START,
            occurred_at=datetime.now(timezone.utc), payload={"origin": "executor"},
        ))
        events = await self.repository.list_events("tenant-a", "run-uuid")
        self.assertEqual(stored.sequence, 1)
        self.assertEqual(events[0].event_id, stored.event_id)
        self.assertTrue(any("CREATE TABLE IF NOT EXISTS platform_run_trace_events" in q for q in self.connection.sql))
        self.assertEqual(await self.repository.list_events("tenant-b", "run-uuid"), [])
