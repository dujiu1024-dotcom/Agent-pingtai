"""Governed consumer for successful Run memory-extraction events.

The handler reloads all source data from tenant-scoped stores. Event payloads are
identifiers only and are never treated as authorization evidence. Automatic
extraction is disabled unless the host supplies an explicit policy callback.
"""
from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from agent_platform.control_plane.sessions_assets import MessageRole
from agent_platform.events.consumer import EventEnvelope
from agent_platform.memory.extraction import MemoryExtractionService, MemoryTurn
from agent_platform.memory.service import MemoryPrincipal


Resolver = Callable[..., Any]


async def _resolve(callback: Resolver, *args: Any) -> Any:
    value = callback(*args)
    return await value if inspect.isawaitable(value) else value


class MemoryExtractionEventHandler:
    """Reload and extract exactly the completed user/assistant Run turn."""

    def __init__(
        self,
        *,
        run_store: Any,
        session_service_resolver: Resolver,
        memory_principal_resolver: Callable[[str, str], Awaitable[MemoryPrincipal] | MemoryPrincipal],
        extraction: MemoryExtractionService,
        policy_enabled: Callable[[str, str | None, str | None], Awaitable[bool] | bool] | None = None,
    ) -> None:
        self.run_store = run_store
        self.session_service_resolver = session_service_resolver
        self.memory_principal_resolver = memory_principal_resolver
        self.extraction = extraction
        # Fail closed: deployments must explicitly opt in through policy.
        self.policy_enabled = policy_enabled or (lambda _tenant, _project, _agent: False)

    async def __call__(self, event: EventEnvelope) -> None:
        if event.topic != "memory.extraction.requested":
            raise ValueError("unsupported memory event topic")

        payload = event.payload
        run_id = payload.get("run_id")
        session_id = payload.get("session_id")
        actor_id = payload.get("actor_id")
        if not all(isinstance(value, str) and value.strip()
                   for value in (run_id, session_id, actor_id)):
            raise ValueError("memory event is missing required identifiers")

        record = await self.run_store.get_task_record(event.tenant_id, run_id)
        if record is None:
            raise ValueError("source Run was not found")
        if (
            str(record.get("run_id")) != run_id
            or str(record.get("status", "")).lower() != "succeeded"
            or str(record.get("created_by")) != actor_id
            or str(record.get("session_id")) != session_id
        ):
            raise PermissionError("memory event does not match a successful owned Run")

        project_id = record.get("project_id")
        agent_id = record.get("agent_id")
        if payload.get("project_id") != project_id or payload.get("agent_id") != agent_id:
            raise PermissionError("memory event runtime context does not match its Run")

        if not await _resolve(self.policy_enabled, event.tenant_id, project_id, agent_id):
            # Policy opt-out is a terminal no-op, not a retryable processing error.
            return

        service = await _resolve(self.session_service_resolver, event.tenant_id)
        repository = getattr(service, "repository", None)
        if repository is None:
            raise RuntimeError("tenant session repository is unavailable")
        repository_tenant = getattr(repository, "tenant_id", event.tenant_id)
        if repository_tenant != event.tenant_id:
            raise PermissionError("session repository tenant mismatch")

        session = await repository.get_session(event.tenant_id, session_id)
        if (
            session is None
            or session.tenant_id != event.tenant_id
            or session.session_id != session_id
            or session.owner_id != actor_id
            or session.project_id != project_id
        ):
            raise PermissionError("session ownership or project does not match the Run")

        principal = await _resolve(
            self.memory_principal_resolver, event.tenant_id, actor_id,
        )
        if (
            not isinstance(principal, MemoryPrincipal)
            or principal.tenant_id != event.tenant_id
            or principal.user_id != actor_id
        ):
            raise PermissionError("memory principal does not match the Run owner")

        messages = await repository.list_messages(event.tenant_id, session_id, limit=500)
        assistant_index = next(
            (index for index in range(len(messages) - 1, -1, -1)
             if messages[index].role is MessageRole.ASSISTANT
             and messages[index].run_id == run_id),
            None,
        )
        if assistant_index is None:
            raise ValueError("completed Run has no persisted assistant message")

        user_message = next(
            (messages[index] for index in range(assistant_index - 1, -1, -1)
             if messages[index].role is MessageRole.USER
             and messages[index].actor_id == actor_id
             and messages[index].run_id in (None, run_id)),
            None,
        )
        if user_message is None:
            raise ValueError("completed Run has no preceding user message from its owner")

        assistant_message = messages[assistant_index]
        metadata = dict(assistant_message.metadata or {})
        message_agent_id = metadata.get("agent_id")
        if message_agent_id is not None and message_agent_id != agent_id:
            raise PermissionError("assistant message Agent does not match the Run")

        turn = MemoryTurn(
            tenant_id=event.tenant_id,
            user_id=actor_id,
            source_ref=(
                f"run:{run_id}:session:{session_id}:"
                f"user:{user_message.message_id}:assistant:{assistant_message.message_id}"
            ),
            user_message=user_message.content,
            assistant_message=assistant_message.content,
            agent_id=agent_id,
            project_id=project_id,
        )
        await self.extraction.propose_from_turn(principal=principal, turn=turn)
