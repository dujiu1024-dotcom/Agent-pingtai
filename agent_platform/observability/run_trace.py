"""Durable, tenant-scoped Run trace events and LangGraph callback bridge.

Run traces are append-only execution evidence. Blackboard remains the source of
truth for Blackboard state; this module links its event IDs into the Run timeline.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping, Protocol
from uuid import uuid4

from agent_platform.observability.redaction import redact_payload


class RunTraceEventType(str, Enum):
    RUN_START = "RUN_START"
    STEP_START = "STEP_START"
    STEP_FINISH = "STEP_FINISH"
    LLM_REQUEST = "LLM_REQUEST"
    LLM_RESPONSE = "LLM_RESPONSE"
    TOOL_CALL = "TOOL_CALL"
    ERROR = "ERROR"
    COMPLETE = "COMPLETE"
    BLACKBOARD_EVENT = "BLACKBOARD_EVENT"


@dataclass(frozen=True)
class RunTraceEvent:
    tenant_id: str
    run_id: str
    event_type: RunTraceEventType
    sequence: int = 0
    event_id: str = ""
    occurred_at: datetime | None = None
    step_id: str | None = None
    parent_event_id: str | None = None
    payload: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.tenant_id.strip() or not self.run_id.strip():
            raise ValueError("trace tenant_id and run_id are required")
        if self.sequence < 0:
            raise ValueError("trace sequence must not be negative")
        if self.occurred_at is not None and (
            self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None
        ):
            raise ValueError("trace timestamp must be timezone-aware")


class RunTraceRepository(Protocol):
    async def append(self, event: RunTraceEvent) -> RunTraceEvent: ...
    async def list_events(
        self, tenant_id: str, run_id: str, *, after_sequence: int = 0, limit: int = 500
    ) -> list[RunTraceEvent]: ...


class InMemoryRunTraceRepository:
    """Thread-safe append-only repository for unit tests and local development."""

    def __init__(self) -> None:
        import asyncio
        self._lock = asyncio.Lock()
        self._events: dict[tuple[str, str], list[RunTraceEvent]] = {}

    async def append(self, event: RunTraceEvent) -> RunTraceEvent:
        from dataclasses import replace
        async with self._lock:
            key = (event.tenant_id, event.run_id)
            rows = self._events.setdefault(key, [])
            if any(row.event_id == event.event_id for row in rows):
                return next(row for row in rows if row.event_id == event.event_id)
            stored = replace(
                event,
                sequence=len(rows) + 1,
                event_id=event.event_id or str(uuid4()),
                occurred_at=event.occurred_at or datetime.now(timezone.utc),
                payload=dict(event.payload or {}),
            )
            rows.append(stored)
            return stored

    async def list_events(
        self, tenant_id: str, run_id: str, *, after_sequence: int = 0, limit: int = 500
    ) -> list[RunTraceEvent]:
        if after_sequence < 0 or not 1 <= limit <= 2000:
            raise ValueError("invalid Run trace pagination")
        async with self._lock:
            rows = self._events.get((tenant_id, run_id), ())
            return list(rows[after_sequence:after_sequence + limit])


class PostgresRunTraceRepository:
    """PostgreSQL append-only Run trace store; schema setup is explicit and idempotent."""

    def __init__(self, *, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    @classmethod
    def from_dsn(cls, *, dsn: str) -> "PostgresRunTraceRepository":
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("PostgreSQL DSN is required")

        async def connect() -> Any:
            try:
                from psycopg import AsyncConnection
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL storage") from exc
            return await AsyncConnection.connect(dsn, row_factory=dict_row)

        return cls(connection_factory=connect)

    @staticmethod
    def _jsonb(value: Mapping[str, Any]) -> Any:
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL storage") from exc
        return Jsonb(dict(value))

    async def ensure_schema(self) -> None:
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS platform_run_trace_events (
                        tenant_id TEXT NOT NULL,
                        run_id TEXT NOT NULL,
                        sequence BIGINT NOT NULL,
                        event_id UUID NOT NULL,
                        event_type TEXT NOT NULL,
                        occurred_at TIMESTAMPTZ NOT NULL,
                        step_id TEXT NULL,
                        parent_event_id UUID NULL,
                        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                        PRIMARY KEY (tenant_id, run_id, sequence),
                        UNIQUE (tenant_id, run_id, event_id)
                    )
                    """
                )
                await connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_platform_run_trace_events_timeline
                    ON platform_run_trace_events (tenant_id, run_id, occurred_at, sequence)
                    """
                )

    @staticmethod
    def _from_row(row: Mapping[str, Any]) -> RunTraceEvent:
        return RunTraceEvent(
            tenant_id=str(row["tenant_id"]),
            run_id=str(row["run_id"]),
            sequence=int(row["sequence"]),
            event_id=str(row["event_id"]),
            event_type=RunTraceEventType(row["event_type"]),
            occurred_at=row["occurred_at"],
            step_id=row["step_id"],
            parent_event_id=str(row["parent_event_id"]) if row["parent_event_id"] else None,
            payload=dict(row["payload"] or {}),
        )

    async def append(self, event: RunTraceEvent) -> RunTraceEvent:
        event_id = event.event_id or str(uuid4())
        occurred_at = event.occurred_at or datetime.now(timezone.utc)
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"{event.tenant_id}:{event.run_id}:trace",),
                )
                cursor = await connection.execute(
                    """SELECT sequence, event_id, event_type, occurred_at, step_id,
                              parent_event_id, payload, tenant_id, run_id
                       FROM platform_run_trace_events
                       WHERE tenant_id=%s AND run_id=%s AND event_id=%s""",
                    (event.tenant_id, event.run_id, event_id),
                )
                existing = await cursor.fetchone()
                if existing is not None:
                    return self._from_row(existing)
                cursor = await connection.execute(
                    """SELECT COALESCE(MAX(sequence), 0) AS latest
                       FROM platform_run_trace_events
                       WHERE tenant_id=%s AND run_id=%s""",
                    (event.tenant_id, event.run_id),
                )
                latest = await cursor.fetchone()
                sequence = int(latest["latest"]) + 1
                await connection.execute(
                    """INSERT INTO platform_run_trace_events
                       (tenant_id, run_id, sequence, event_id, event_type, occurred_at,
                        step_id, parent_event_id, payload)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        event.tenant_id, event.run_id, sequence, event_id,
                        event.event_type.value, occurred_at, event.step_id,
                        event.parent_event_id, self._jsonb(event.payload or {}),
                    ),
                )
        from dataclasses import replace
        return replace(
            event, sequence=sequence, event_id=event_id, occurred_at=occurred_at,
            payload=dict(event.payload or {}),
        )

    async def list_events(
        self, tenant_id: str, run_id: str, *, after_sequence: int = 0, limit: int = 500
    ) -> list[RunTraceEvent]:
        if after_sequence < 0 or not 1 <= limit <= 2000:
            raise ValueError("invalid Run trace pagination")
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """SELECT tenant_id, run_id, sequence, event_id, event_type,
                          occurred_at, step_id, parent_event_id, payload
                   FROM platform_run_trace_events
                   WHERE tenant_id=%s AND run_id=%s AND sequence>%s
                   ORDER BY sequence ASC LIMIT %s""",
                (tenant_id, run_id, after_sequence, limit),
            )
            rows = await cursor.fetchall()
        return [self._from_row(row) for row in rows]


class LangfuseRunTraceExporter:
    """Export normalized trace events as Langfuse observations without SDK coupling."""

    def __init__(self, observation_sink: Any) -> None:
        self.observation_sink = observation_sink

    def export(self, event: RunTraceEvent) -> None:
        with self.observation_sink.observe(
            name=f"run.{event.event_type.value.lower()}",
            input={"sequence": event.sequence, "step_id": event.step_id},
            metadata={
                "tenant_id": event.tenant_id, "run_id": event.run_id,
                "event_id": event.event_id, "event_type": event.event_type.value,
            },
        ) as observation:
            observation.update(output=redact_payload(dict(event.payload or {})))


class RunTraceService:
    def __init__(
        self, *, repository: RunTraceRepository, exporter: LangfuseRunTraceExporter | None = None,
        blackboard_replay: Any | None = None,
    ) -> None:
        self.repository = repository
        self.exporter = exporter
        self.blackboard_replay = blackboard_replay

    async def emit(
        self, *, tenant_id: str, run_id: str, event_type: RunTraceEventType,
        payload: Mapping[str, Any] | None = None, step_id: str | None = None,
        parent_event_id: str | None = None, event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> RunTraceEvent:
        safe_payload = redact_payload(dict(payload or {}), max_text_chars=2000)
        if not isinstance(safe_payload, dict):
            safe_payload = {"value": safe_payload}
        event = await self.repository.append(RunTraceEvent(
            tenant_id=tenant_id, run_id=run_id, event_type=event_type,
            step_id=step_id, parent_event_id=parent_event_id, payload=safe_payload,
            event_id=event_id or "", occurred_at=occurred_at,
        ))
        if self.exporter is not None:
            try:
                self.exporter.export(event)
            except Exception:
                # Langfuse is an observability projection; PostgreSQL remains authoritative.
                pass
        return event

    async def emit_blackboard_event(
        self, *, tenant_id: str, run_id: str, event: Any
    ) -> RunTraceEvent:
        """Link a #13 Blackboard event into this Run timeline; do not duplicate its payload."""
        scope = getattr(getattr(event, "scope", None), "value", None)
        scope_id = getattr(event, "scope_id", None)
        if scope != "run" or scope_id != run_id:
            raise ValueError("Blackboard event must be scoped to the target Run")
        return await self.emit(
            tenant_id=tenant_id, run_id=run_id,
            event_type=RunTraceEventType.BLACKBOARD_EVENT,
            event_id=getattr(event, "event_id", None),
            occurred_at=getattr(event, "occurred_at", None),
            payload={
                "blackboard_event_id": getattr(event, "event_id", None),
                "event_type": getattr(event, "event_type", None),
                "entry_id": getattr(event, "entry_id", None),
                "entry_version": getattr(event, "entry_version", None),
                "actor_id": getattr(event, "actor_id", None),
                "correlation_id": getattr(event, "correlation_id", None),
            },
        )


    async def capture_blackboard_events(self, *, tenant_id: str, run_id: str) -> int:
        """Import the durable #13 RUN-scope event stream into this Run timeline."""
        if self.blackboard_replay is None:
            return 0
        from agent_platform.contracts import Scope
        cursor = None
        captured = 0
        while True:
            events = await self.blackboard_replay.list_after(
                cursor=cursor, scope=Scope.RUN, scope_id=run_id, limit=1000,
            )
            for event in events:
                await self.emit_blackboard_event(
                    tenant_id=tenant_id, run_id=run_id, event=event,
                )
                captured += 1
            if len(events) < 1000:
                return captured
            cursor = self.blackboard_replay.cursor_for(events[-1])


try:
    from langchain_core.callbacks.base import AsyncCallbackHandler as _AsyncCallbackHandler
except ImportError:
    class _AsyncCallbackHandler:  # type: ignore[no-redef]
        pass


class RunTraceCallbackHandler(_AsyncCallbackHandler):
    """Capture LangGraph/LangChain chain, LLM and tool lifecycle callbacks."""

    def __init__(self, *, trace: RunTraceService, tenant_id: str, run_id: str) -> None:
        self.trace = trace
        self.tenant_id = tenant_id
        self.run_id = run_id
        self.raise_error = True

    async def on_chain_start(self, serialized: Any, inputs: Any, *, run_id: Any, parent_run_id: Any = None, metadata: Any = None, name: str | None = None, **kwargs: Any) -> None:
        step_id = str(run_id)
        await self.trace.emit(
            tenant_id=self.tenant_id, run_id=self.run_id,
            event_type=RunTraceEventType.STEP_START, step_id=step_id,
            payload={"name": name or (serialized or {}).get("name", "chain"), "parent_run_id": str(parent_run_id) if parent_run_id else None, "input_keys": sorted(inputs) if isinstance(inputs, Mapping) else []},
        )

    async def on_chain_end(self, outputs: Any, *, run_id: Any, **kwargs: Any) -> None:
        await self.trace.emit(
            tenant_id=self.tenant_id, run_id=self.run_id,
            event_type=RunTraceEventType.STEP_FINISH, step_id=str(run_id),
            payload={"output_keys": sorted(outputs) if isinstance(outputs, Mapping) else [], "status": "succeeded"},
        )

    async def on_chain_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        await self.trace.emit(
            tenant_id=self.tenant_id, run_id=self.run_id,
            event_type=RunTraceEventType.ERROR, step_id=str(run_id),
            payload={"component": "chain", "error_type": type(error).__name__},
        )

    async def on_llm_start(self, serialized: Any, prompts: Any, *, run_id: Any, parent_run_id: Any = None, **kwargs: Any) -> None:
        await self.trace.emit(
            tenant_id=self.tenant_id, run_id=self.run_id,
            event_type=RunTraceEventType.LLM_REQUEST, step_id=str(parent_run_id or run_id),
            payload={"model": (serialized or {}).get("name") if isinstance(serialized, Mapping) else None, "prompt_count": len(prompts) if prompts else 0},
        )

    async def on_chat_model_start(self, serialized: Any, messages: Any, *, run_id: Any, parent_run_id: Any = None, **kwargs: Any) -> None:
        await self.trace.emit(
            tenant_id=self.tenant_id, run_id=self.run_id,
            event_type=RunTraceEventType.LLM_REQUEST, step_id=str(parent_run_id or run_id),
            payload={"model": (serialized or {}).get("name") if isinstance(serialized, Mapping) else None, "message_batch_count": len(messages) if messages else 0},
        )

    async def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
        generations = getattr(response, "generations", ()) or ()
        await self.trace.emit(
            tenant_id=self.tenant_id, run_id=self.run_id,
            event_type=RunTraceEventType.LLM_RESPONSE, step_id=str(run_id),
            payload={"generation_count": sum(len(batch) for batch in generations), "status": "succeeded"},
        )

    async def on_llm_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        await self.trace.emit(
            tenant_id=self.tenant_id, run_id=self.run_id,
            event_type=RunTraceEventType.ERROR, step_id=str(run_id),
            payload={"component": "llm", "error_type": type(error).__name__},
        )

    async def on_tool_start(self, serialized: Any, input_str: str, *, run_id: Any, parent_run_id: Any = None, name: str | None = None, **kwargs: Any) -> None:
        await self.trace.emit(
            tenant_id=self.tenant_id, run_id=self.run_id,
            event_type=RunTraceEventType.TOOL_CALL, step_id=str(parent_run_id or run_id),
            payload={"tool": name or (serialized or {}).get("name", "tool"), "input_chars": len(input_str or "")},
        )

    async def on_tool_end(self, output: Any, *, run_id: Any, **kwargs: Any) -> None:
        await self.trace.emit(
            tenant_id=self.tenant_id, run_id=self.run_id,
            event_type=RunTraceEventType.STEP_FINISH, step_id=str(run_id),
            payload={"component": "tool", "status": "succeeded"},
        )

    async def on_tool_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        await self.trace.emit(
            tenant_id=self.tenant_id, run_id=self.run_id,
            event_type=RunTraceEventType.ERROR, step_id=str(run_id),
            payload={"component": "tool", "error_type": type(error).__name__},
        )
