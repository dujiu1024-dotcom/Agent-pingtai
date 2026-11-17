"""Deterministic, tenant-scoped replay over persisted Run context and trace events."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping
from uuid import uuid4
from agent_platform.observability.run_trace import RunTraceEvent, RunTraceRepository

class ReplayNotFound(LookupError):
    pass

class ReplayContextIncomplete(ValueError):
    pass

@dataclass
class ReplaySession:
    session_id: str
    tenant_id: str
    run_id: str
    events: tuple[RunTraceEvent, ...]
    context: Mapping[str, Any]
    cursor: int = 0
    paused: bool = False

class RunReplayService:
    """Reconstruct context from durable snapshots; playback cursors are ephemeral."""
    def __init__(self, *, lifecycle_store: Any, trace_repository: RunTraceRepository,
        resolve_context_service: Callable[[str], Awaitable[Any]],
        resolve_registry_snapshot_service: Callable[[str], Awaitable[Any]] | None = None,
        resolve_memory_snapshot: Callable[[str, str], Awaitable[Any]] | None = None,
        rerun_step: Callable[..., Awaitable[Any]] | None = None) -> None:
        self.lifecycle_store = lifecycle_store
        self.trace_repository = trace_repository
        self.resolve_context_service = resolve_context_service
        self.resolve_registry_snapshot_service = resolve_registry_snapshot_service
        self.resolve_memory_snapshot = resolve_memory_snapshot
        self.rerun_step = rerun_step
        self._sessions: dict[tuple[str, str, str], ReplaySession] = {}
        self._lock = asyncio.Lock()

    async def _reconstruct(self, tenant_id: str, run_id: str) -> dict[str, Any]:
        context_service = await self.resolve_context_service(tenant_id)
        try:
            run_context = await context_service.rebuild(tenant_id=tenant_id, run_id=run_id)
        except (KeyError, LookupError) as exc:
            raise ReplayContextIncomplete("persisted Run context snapshot is missing") from exc
        if not isinstance(run_context, Mapping):
            raise ReplayContextIncomplete("persisted Run context snapshot is invalid")
        required = ("phase", "version", "memory_snapshot_ref")
        missing = [key for key in required if key not in run_context or run_context[key] is None]
        if missing:
            raise ReplayContextIncomplete(
                "persisted Run context is missing required replay fields: " + ", ".join(missing))
        restored: dict[str, Any] = {
            "phase": run_context["phase"], "version": run_context["version"],
            "memory_snapshot_ref": run_context["memory_snapshot_ref"],
            "run_context": dict(run_context),
        }
        if self.resolve_registry_snapshot_service is not None:
            registry_service = await self.resolve_registry_snapshot_service(tenant_id)
            snapshot = await registry_service.get(tenant_id=tenant_id, run_id=run_id)
            if snapshot.tenant_id != tenant_id or snapshot.run_id != run_id:
                raise ReplayContextIncomplete("registry snapshot scope mismatch")
            restored["registry_snapshot"] = {
                "entries": [dict(entry) for entry in snapshot.entries], "digest": snapshot.digest}
        if self.resolve_memory_snapshot is not None:
            memory = await self.resolve_memory_snapshot(tenant_id, str(run_context["memory_snapshot_ref"]))
            if memory is None:
                raise ReplayContextIncomplete("referenced memory snapshot is missing")
            restored["memory_snapshot"] = memory
        return restored

    async def open(self, *, tenant_id: str, run_id: str) -> ReplaySession:
        if await self.lifecycle_store.get(tenant_id, run_id) is None:
            raise ReplayNotFound("Run not found")
        context = await self._reconstruct(tenant_id, run_id)
        events: list[RunTraceEvent] = []
        after = 0
        while True:
            page = await self.trace_repository.list_events(tenant_id, run_id, after_sequence=after, limit=2000)
            if not page:
                break
            events.extend(page)
            after = page[-1].sequence
            if len(page) < 2000:
                break
        session = ReplaySession(str(uuid4()), tenant_id, run_id, tuple(events), context)
        async with self._lock:
            self._sessions[(tenant_id, run_id, session.session_id)] = session
        return session

    async def _session(self, tenant_id: str, run_id: str, session_id: str) -> ReplaySession:
        async with self._lock:
            session = self._sessions.get((tenant_id, run_id, session_id))
        if session is None:
            raise ReplayNotFound("Replay session not found")
        return session

    @staticmethod
    def _event(event: RunTraceEvent) -> dict[str, Any]:
        return {"event_id": event.event_id, "sequence": event.sequence,
            "event_type": event.event_type.value,
            "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
            "step_id": event.step_id, "parent_event_id": event.parent_event_id,
            "payload": dict(event.payload or {})}

    def _state(self, session: ReplaySession, event: RunTraceEvent | None = None) -> dict[str, Any]:
        return {"session_id": session.session_id, "run_id": session.run_id,
            "cursor": session.cursor, "total": len(session.events), "paused": session.paused,
            "event": self._event(event) if event is not None else None,
            "context": dict(session.context)}

    async def step(self, *, tenant_id: str, run_id: str, session_id: str) -> dict[str, Any]:
        session = await self._session(tenant_id, run_id, session_id)
        if session.paused or session.cursor >= len(session.events):
            return self._state(session)
        event = session.events[session.cursor]
        session.cursor += 1
        return self._state(session, event)

    async def pause(self, *, tenant_id: str, run_id: str, session_id: str) -> dict[str, Any]:
        session = await self._session(tenant_id, run_id, session_id)
        session.paused = True
        return self._state(session)

    async def resume(self, *, tenant_id: str, run_id: str, session_id: str) -> dict[str, Any]:
        session = await self._session(tenant_id, run_id, session_id)
        session.paused = False
        return self._state(session)

    async def fast_forward(self, *, tenant_id: str, run_id: str, session_id: str) -> dict[str, Any]:
        session = await self._session(tenant_id, run_id, session_id)
        session.cursor = len(session.events)
        return self._state(session, session.events[-1] if session.events else None)

    async def back(self, *, tenant_id: str, run_id: str, session_id: str) -> dict[str, Any]:
        session = await self._session(tenant_id, run_id, session_id)
        session.cursor = max(0, session.cursor - 1)
        event = session.events[session.cursor - 1] if session.cursor else None
        return self._state(session, event)

    async def seek(self, *, tenant_id: str, run_id: str, session_id: str, sequence: int) -> dict[str, Any]:
        session = await self._session(tenant_id, run_id, session_id)
        if sequence < 0 or sequence > len(session.events):
            raise ValueError("replay sequence is outside the persisted event range")
        session.cursor = sequence
        return self._state(session, session.events[sequence - 1] if sequence else None)

    async def rerun(self, *, tenant_id: str, run_id: str, session_id: str, sequence: int) -> dict[str, Any]:
        if self.rerun_step is None:
            raise ValueError("step rerun executor is not configured")
        session = await self._session(tenant_id, run_id, session_id)
        event = next((item for item in session.events if item.sequence == sequence), None)
        if event is None or not event.step_id:
            raise ValueError("selected persisted event has no replayable step_id")
        payload = dict(event.payload or {})
        if "replay_input" not in payload and "replay_input_ref" not in payload:
            raise ValueError("persisted step input/reference is missing; rerun refused")
        result = await self.rerun_step(tenant_id=tenant_id, run_id=run_id,
            step_id=event.step_id, event=event, context=dict(session.context))
        return {"sequence": sequence, "step_id": event.step_id, "result": result}
