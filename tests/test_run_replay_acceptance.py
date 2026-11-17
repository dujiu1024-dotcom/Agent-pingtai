from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from agent_platform.observability.run_trace import (
    InMemoryRunTraceRepository,
    RunTraceEventType,
    RunTraceService,
)
from agent_platform.runtime.replay import RunReplayService


class ReplayFidelityAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    """Deterministic contract acceptance for persisted Run replay reconstruction.

    This fixture-based test proves fidelity of persisted context and event replay.
    It does not claim equivalence against a live model/provider execution.
    """

    async def test_replay_context_and_event_timeline_match_persisted_run_at_least_95_percent(
        self,
    ) -> None:
        repository = InMemoryRunTraceRepository()
        trace = RunTraceService(repository=repository)
        run_ids = [f"run-{index}" for index in range(5)]
        snapshots: dict[tuple[str, str], dict[str, Any]] = {}
        memories: dict[str, dict[str, Any]] = {}
        registry_entries: dict[tuple[str, str], tuple[dict[str, Any], ...]] = {}

        class LifecycleStore:
            async def get(self, tenant_id: str, run_id: str) -> Any:
                if (tenant_id, run_id) not in snapshots:
                    return None
                return SimpleNamespace(tenant_id=tenant_id, run_id=run_id)

        class ContextService:
            async def rebuild(self, *, tenant_id: str, run_id: str) -> dict[str, Any]:
                return dict(snapshots[(tenant_id, run_id)])

        class RegistryStore:
            async def get(self, *, tenant_id: str, run_id: str) -> Any:
                entries = registry_entries[(tenant_id, run_id)]
                return SimpleNamespace(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    entries=entries,
                    digest=f"digest-{run_id}",
                )

        async def resolve_context(_tenant_id: str) -> ContextService:
            return ContextService()

        async def resolve_registry(_tenant_id: str) -> RegistryStore:
            return RegistryStore()

        async def resolve_memory(_tenant_id: str, ref: str) -> dict[str, Any] | None:
            return memories.get(ref)

        replay = RunReplayService(
            lifecycle_store=LifecycleStore(),
            trace_repository=repository,
            resolve_context_service=resolve_context,
            resolve_registry_snapshot_service=resolve_registry,
            resolve_memory_snapshot=resolve_memory,
        )

        event_types = (
            RunTraceEventType.RUN_START,
            RunTraceEventType.STEP_START,
            RunTraceEventType.LLM_REQUEST,
            RunTraceEventType.LLM_RESPONSE,
            RunTraceEventType.TOOL_CALL,
            RunTraceEventType.STEP_FINISH,
            RunTraceEventType.ERROR,
            RunTraceEventType.COMPLETE,
        )
        expected_events = 0
        matched_events = 0

        for index, run_id in enumerate(run_ids):
            tenant_id = "tenant-replay"
            memory_ref = f"memory://{tenant_id}/{run_id}/v{index + 1}"
            context_snapshot = {
                "phase": "execute" if index % 2 else "planning",
                "version": index + 3,
                "memory_snapshot_ref": memory_ref,
                "objective": f"complete-task-{index}",
                "request": {"task": f"task-{index}", "constraints": ["safe", "audited"]},
                "state": {
                    "decisions": [{"id": f"decision-{index}", "approved": True}],
                    "evidence": [{"ref": f"evidence://{index}", "confidence": 0.97}],
                },
                "token_budget": {"input": 4096 + index, "output": 1024},
            }
            snapshots[(tenant_id, run_id)] = context_snapshot
            memories[memory_ref] = {
                "ref": memory_ref,
                "facts": [f"fact-{index}-a", f"fact-{index}-b"],
                "source_refs": [f"session://{tenant_id}/{run_id}"],
            }
            registry_entries[(tenant_id, run_id)] = (
                {"kind": "agent", "item_id": "planner", "version": index + 1},
                {"kind": "tool", "item_id": "search", "version": index + 2},
            )

            for event_index, event_type in enumerate(event_types):
                await trace.emit(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    event_type=event_type,
                    step_id=f"step-{event_index}" if event_type in {
                        RunTraceEventType.STEP_START,
                        RunTraceEventType.LLM_REQUEST,
                        RunTraceEventType.LLM_RESPONSE,
                        RunTraceEventType.TOOL_CALL,
                        RunTraceEventType.STEP_FINISH,
                        RunTraceEventType.ERROR,
                    } else None,
                    payload={
                        "case": index,
                        "ordinal": event_index,
                        "input": {"query": f"query-{index}-{event_index}"},
                        "result": {"status": "recorded"},
                    },
                )

            persisted = await repository.list_events(tenant_id, run_id, limit=2000)
            session = await replay.open(tenant_id=tenant_id, run_id=run_id)

            self.assertEqual(dict(session.context["run_context"]), context_snapshot)
            self.assertEqual(session.context["memory_snapshot"], memories[memory_ref])
            self.assertEqual(
                session.context["registry_snapshot"]["entries"],
                list(registry_entries[(tenant_id, run_id)]),
            )
            self.assertEqual(session.events, tuple(persisted))

            replayed_timeline = []
            while True:
                state = await replay.step(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    session_id=session.session_id,
                )
                event = state["event"]
                if event is None:
                    break
                replayed_timeline.append(event)

            expected_timeline = [replay._event(event) for event in persisted]
            expected_events += len(expected_timeline)
            matched_events += sum(
                actual == expected
                for actual, expected in zip(replayed_timeline, expected_timeline)
            )
            self.assertEqual(replayed_timeline, expected_timeline)

        fidelity = matched_events / expected_events if expected_events else 0.0
        self.assertGreaterEqual(fidelity, 0.95)
        self.assertEqual(fidelity, 1.0)


if __name__ == "__main__":
    unittest.main()
