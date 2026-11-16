from __future__ import annotations

import unittest
from types import SimpleNamespace

from agent_platform.control_plane.sessions_assets import (
    ConversationMessage, ConversationSession, MessageRole,
)
from agent_platform.events.consumer import EventEnvelope
from agent_platform.memory.event_handler import (
    MemoryExtractionEventHandler,
    create_memory_extraction_event_worker,
)
from agent_platform.memory.service import MemoryPrincipal


TENANT = "tenant-a"
ACTOR = "user-a"
SESSION = "session-a"
RUN = "00000000-0000-0000-0000-000000000123"
EVENT = "00000000-0000-0000-0000-000000000456"


class FakeRepository:
    tenant_id = TENANT

    def __init__(self, *, owner=ACTOR):
        self.session = ConversationSession(
            tenant_id=TENANT, session_id=SESSION, owner_id=owner,
            title="test", project_id="project-a",
        )
        self.messages = (
            ConversationMessage(
                tenant_id=TENANT, message_id="old-user", session_id=SESSION,
                actor_id=ACTOR, role=MessageRole.USER, content="old question",
            ),
            ConversationMessage(
                tenant_id=TENANT, message_id="old-assistant", session_id=SESSION,
                actor_id="agent", role=MessageRole.ASSISTANT, content="old answer",
                run_id="another-run",
            ),
            ConversationMessage(
                tenant_id=TENANT, message_id="run-user", session_id=SESSION,
                actor_id=ACTOR, role=MessageRole.USER, content="remember this",
            ),
            ConversationMessage(
                tenant_id=TENANT, message_id="run-assistant", session_id=SESSION,
                actor_id="agent", role=MessageRole.ASSISTANT, content="I will remember",
                run_id=RUN, metadata={"agent_id": "agent-a"},
            ),
        )

    async def get_session(self, tenant_id, session_id):
        return self.session if (tenant_id, session_id) == (TENANT, SESSION) else None

    async def list_messages(self, tenant_id, session_id, limit=100):
        return self.messages


class FakeRunStore:
    def __init__(self, **overrides):
        self.record = {
            "tenant_id": TENANT, "run_id": RUN, "task_id": RUN,
            "session_id": SESSION, "created_by": ACTOR,
            "project_id": "project-a", "agent_id": "agent-a",
            "status": "succeeded",
        }
        self.record.update(overrides)

    async def get_task_record(self, tenant_id, task_id):
        return self.record if tenant_id == TENANT and task_id == RUN else None


class FakeExtraction:
    def __init__(self):
        self.turns = []

    async def propose_from_turn(self, *, principal, turn):
        self.turns.append((principal, turn))
        return []


def make_event(**payload):
    values = {
        "run_id": RUN, "session_id": SESSION, "actor_id": ACTOR,
        "project_id": "project-a", "agent_id": "agent-a",
    }
    values.update(payload)
    return EventEnvelope(
        tenant_id=TENANT, topic="memory.extraction.requested",
        event_id=EVENT, message_id="1-0", payload=values,
    )


class MemoryExtractionEventHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repository = FakeRepository()
        self.service = SimpleNamespace(repository=self.repository)
        self.extraction = FakeExtraction()
        self.principal = MemoryPrincipal(
            tenant_id=TENANT, user_id=ACTOR,
            project_ids=frozenset({"project-a"}),
            agent_ids=frozenset({"agent-a"}),
        )

    def handler(self, *, run_store=None, principal=None, policy=True, repository=None):
        service = SimpleNamespace(repository=repository or self.repository)
        return MemoryExtractionEventHandler(
            run_store=run_store or FakeRunStore(),
            session_service_resolver=lambda tenant: service,
            memory_principal_resolver=lambda tenant, actor: principal or self.principal,
            extraction=self.extraction,
            policy_enabled=lambda tenant, project, agent: policy,
        )

    async def test_extracts_only_assistant_turn_for_source_run(self):
        await self.handler()(make_event())
        self.assertEqual(len(self.extraction.turns), 1)
        principal, turn = self.extraction.turns[0]
        self.assertEqual(principal.user_id, ACTOR)
        self.assertEqual(turn.user_message, "remember this")
        self.assertEqual(turn.assistant_message, "I will remember")
        self.assertIn("run-user", turn.source_ref)
        self.assertIn("run-assistant", turn.source_ref)

    async def test_policy_is_fail_closed_by_default(self):
        handler = MemoryExtractionEventHandler(
            run_store=FakeRunStore(),
            session_service_resolver=lambda tenant: self.service,
            memory_principal_resolver=lambda tenant, actor: self.principal,
            extraction=self.extraction,
        )
        await handler(make_event())
        self.assertEqual(self.extraction.turns, [])

    async def test_policy_disabled_is_terminal_noop(self):
        await self.handler(policy=False)(make_event())
        self.assertEqual(self.extraction.turns, [])

    async def test_rejects_run_or_actor_mismatch(self):
        with self.assertRaises(PermissionError):
            await self.handler(run_store=FakeRunStore(created_by="other"))(make_event())
        with self.assertRaises(PermissionError):
            await self.handler()(make_event(actor_id="other"))

    async def test_rejects_session_owner_mismatch(self):
        repository = FakeRepository(owner="other")
        with self.assertRaises(PermissionError):
            await self.handler(repository=repository)(make_event())
        self.assertEqual(self.extraction.turns, [])

    async def test_rejects_memory_principal_tenant_mismatch(self):
        principal = MemoryPrincipal(tenant_id="tenant-b", user_id=ACTOR)
        with self.assertRaises(PermissionError):
            await self.handler(principal=principal)(make_event())
        self.assertEqual(self.extraction.turns, [])

    async def test_rejects_non_succeeded_run(self):
        with self.assertRaises(PermissionError):
            await self.handler(run_store=FakeRunStore(status="failed"))(make_event())

    async def test_uses_run_scoped_input_and_output_when_session_transcript_is_absent(self):
        repository = FakeRepository()
        repository.messages = ()
        run_store = FakeRunStore(
            input={"text": "task-specific question"},
            output={"text": "task-specific answer"},
        )
        await self.handler(run_store=run_store, repository=repository)(make_event())
        self.assertEqual(len(self.extraction.turns), 1)
        _, turn = self.extraction.turns[0]
        self.assertEqual(turn.user_message, "task-specific question")
        self.assertEqual(turn.assistant_message, "task-specific answer")
        self.assertIn(":task-output", turn.source_ref)

    async def test_waits_for_successful_run_output_to_be_persisted(self):
        repository = FakeRepository()
        repository.messages = ()
        with self.assertRaisesRegex(RuntimeError, "output is not persisted"):
            await self.handler(repository=repository)(make_event())
        self.assertEqual(self.extraction.turns, [])

    async def test_factory_composes_tenant_scoped_consumer(self):
        transport = SimpleNamespace()
        receipts = SimpleNamespace()
        worker = create_memory_extraction_event_worker(
            tenant_id=TENANT,
            consumer_name="memory-worker-a",
            transport=transport,
            receipts=receipts,
            run_store=FakeRunStore(),
            session_service_resolver=lambda tenant: self.service,
            memory_principal_resolver=lambda tenant, actor: self.principal,
            extraction=self.extraction,
            policy_enabled=lambda tenant, project, agent: True,
        )
        self.assertEqual(worker.consumer.tenant_id, TENANT)
        self.assertEqual(worker.consumer.topic, "memory.extraction.requested")
        self.assertIs(worker.consumer.transport, transport)
        self.assertIs(worker.consumer.receipts, receipts)

    async def test_rejects_run_record_from_another_tenant(self):
        with self.assertRaises(PermissionError):
            await self.handler(run_store=FakeRunStore(tenant_id="tenant-b"))(make_event())

    async def test_rejects_inactive_session(self):
        repository = FakeRepository()
        repository.session = ConversationSession(
            tenant_id=TENANT, session_id=SESSION, owner_id=ACTOR,
            title="test", project_id="project-a", status="deleted",
        )
        with self.assertRaises(PermissionError):
            await self.handler(repository=repository)(make_event())


if __name__ == "__main__":
    unittest.main()
