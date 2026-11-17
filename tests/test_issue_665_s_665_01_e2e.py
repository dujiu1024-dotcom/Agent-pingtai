from __future__ import annotations

import asyncio
import unittest
from typing import Any

from agent_platform.contracts import BlackboardEntry, EntryKind
from agent_platform.control_plane.registry import InMemoryRegistryRepository, RegistryService
from agent_platform.events.outbox import OutboxRecord, OutboxRelay
from agent_platform.runtime.lifecycle import InMemoryRunStateStore, RunLifecycleManager, RunStatus
from agent_platform.workflows.blackboard_services import BlackboardMultiExpertServices
from agent_platform.workflows.multi_expert import build_multi_expert_workflow


TENANT = "tenant-s665"
AGENT = "delivery-agent"
TASK = "task-s665"
RUN = "run-s665"


class JourneyBlackboard:
    def __init__(self) -> None:
        self.tenant_id = TENANT
        self.entries: dict[str, BlackboardEntry] = {}

    async def get(self, entry_id: str) -> BlackboardEntry | None:
        return self.entries.get(entry_id)

    async def propose(self, entry: BlackboardEntry) -> BlackboardEntry:
        if entry.entry_id in self.entries:
            raise RuntimeError("duplicate Blackboard entry")
        self.entries[entry.entry_id] = entry
        return entry


class JourneyCallbacks:
    async def plan_experts(self, *, objective: str, run_context: Any):
        return [{"expert_id": AGENT, "required_capabilities": ["delivery.execute"]}]

    async def run_expert(self, *, assignment: Any, objective: str, run_context: Any):
        return {"expert_id": assignment["expert_id"], "result": "artifact prepared"}

    async def synthesize(self, *, objective: str, contributions: Any, run_context: Any):
        return {"summary": "delivery artifact ready"}

    async def review(self, *, synthesis: Any, contributions: Any, run_context: Any):
        return {"conflicts": [], "requires_approval": False}

    async def resolve_conflicts(self, *, synthesis: Any, conflicts: Any, run_context: Any):
        return {"synthesis": dict(synthesis), "requires_approval": False}

    async def prepare_approval(self, *, state: Any):
        return {}

    async def apply_approval(self, *, state: Any, decision: Any):
        return {"approved": decision.get("approved") is True}

    async def archive(self, *, state: Any):
        return {"artifact": "delivery-result", "run_id": state["run_id"]}

    async def reject(self, *, state: Any):
        return {"reason": "rejected"}


class NotificationOutboxStore:
    def __init__(self, record: OutboxRecord) -> None:
        self.record = record
        self.published: list[str] = []

    async def claim_batch(self, *, limit: int):
        return [self.record][:limit]

    async def mark_published(self, record: OutboxRecord):
        self.published.append(record.event_id)
        return True

    async def mark_failed(self, record: OutboxRecord, error: str):
        raise AssertionError(f"notification outbox unexpectedly failed: {error}")


class NotificationTransport:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def publish(self, **message: Any):
        self.messages.append(message)
        return "stream-1"


class AuditSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def record(self, **event: Any):
        self.events.append(event)


class Issue665S66501E2ETests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_workflow_run_blackboard_notification_chain(self):
        # Agent definition is created and published through the control-plane registry.
        registry = RegistryService(InMemoryRegistryRepository())
        draft = await registry.create_draft(
            tenant_id=TENANT,
            kind="agent",
            item_id=AGENT,
            definition={
                "name": "Delivery Agent",
                "instructions": "Prepare a delivery artifact from the supplied objective.",
                "capabilities": ["delivery.execute"],
            },
            actor_id="operator",
        )
        published = await registry.publish(
            tenant_id=TENANT, kind="agent", item_id=AGENT, version=draft.version,
        )
        self.assertEqual(published.status, "published")
        resolved_agent = await registry.resolve_active(
            tenant_id=TENANT, kind="agent", item_id=AGENT,
        )
        self.assertEqual(resolved_agent.definition["capabilities"], ["delivery.execute"])

        # Create and start a real platform Run lifecycle record before graph execution.
        run_store = InMemoryRunStateStore()
        lifecycle = RunLifecycleManager(run_store)
        await run_store.create(TENANT, RUN)
        started = await lifecycle.start(TENANT, RUN)
        self.assertEqual(started.status, RunStatus.RUNNING)

        blackboard = JourneyBlackboard()
        services = BlackboardMultiExpertServices(
            tenant_id=TENANT, repository=blackboard, callbacks=JourneyCallbacks(),
        )
        try:
            from langgraph.checkpoint.memory import InMemorySaver
        except ImportError as exc:
            self.skipTest(f"LangGraph test dependency unavailable: {exc}")
        graph = build_multi_expert_workflow(services=services, checkpointer=InMemorySaver())
        result = await graph.ainvoke(
            {
                "tenant_id": TENANT,
                "task_id": TASK,
                "run_id": RUN,
                "objective": "prepare a delivery artifact",
                "run_context": {"agent_id": AGENT, "agent_version": resolved_agent.version},
            },
            config={"configurable": {"thread_id": f"{TENANT}:{RUN}"}},
        )
        self.assertEqual(result["status"], "completed")
        final_run = await lifecycle.finish(TENANT, RUN, RunStatus.SUCCEEDED)
        self.assertEqual(final_run.status, RunStatus.SUCCEEDED)

        # Verify workflow claims and final artifact are persisted under this task scope.
        entries = list(blackboard.entries.values())
        claims = [entry for entry in entries if entry.kind is EntryKind.CLAIM]
        artifacts = [entry for entry in entries if entry.kind is EntryKind.ARTIFACT]
        self.assertEqual(len(claims), 1)
        self.assertEqual(len(artifacts), 2)  # synthesis + accepted final artifact
        final_artifact = next(
            entry for entry in artifacts if entry.status.value == "accepted"
        )
        self.assertEqual(final_artifact.scope_id, TASK)
        self.assertIn(claims[0].entry_id, final_artifact.source_refs)

        # The completed Run emits its notification through the outbox relay.
        record = OutboxRecord(
            tenant_id=TENANT,
            outbox_id=1,
            event_id="00000000-0000-0000-0000-000000006650",
            topic="notification.run.completed",
            payload={
                "run_id": RUN,
                "task_id": TASK,
                "status": final_run.status.value,
                "artifact_entry_id": final_artifact.entry_id,
            },
            lease_token="00000000-0000-0000-0000-000000006651",
        )
        outbox = NotificationOutboxStore(record)
        transport = NotificationTransport()
        audit = AuditSink()
        relay_result = await OutboxRelay(
            store=outbox, transport=transport, audit_sink=audit,
        ).publish_pending()
        self.assertEqual(relay_result, {"claimed": 1, "published": 1, "failed": 0})
        self.assertEqual(outbox.published, [record.event_id])
        self.assertEqual(transport.messages[0]["topic"], "notification.run.completed")
        self.assertEqual(transport.messages[0]["payload"]["artifact_entry_id"], final_artifact.entry_id)
        self.assertEqual(audit.events[-1]["outcome"], "succeeded")


if __name__ == "__main__":
    unittest.main()
