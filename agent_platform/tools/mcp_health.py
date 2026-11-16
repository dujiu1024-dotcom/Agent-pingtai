"""MCP server health probing with tenant-scoped durable status.

A trusted host supplies the session factory; registry content never supplies
transport endpoints, process commands, or credentials.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncContextManager, Callable, Protocol


class MCPHealthError(RuntimeError):
    pass


@dataclass(frozen=True)
class MCPHealthRecord:
    tenant_id: str
    server_id: str
    status: str
    checked_at: datetime
    latency_ms: int
    expected_tools: tuple[str, ...]
    discovered_tools: tuple[str, ...]
    error_code: str | None = None


class MCPHealthSession(Protocol):
    async def ping(self) -> Any: ...
    async def list_tools(self) -> Any: ...


class MCPHealthStore(Protocol):
    async def save(self, record: MCPHealthRecord) -> None: ...
    async def get(self, tenant_id: str, server_id: str) -> MCPHealthRecord | None: ...


SessionFactory = Callable[[str, str], AsyncContextManager[MCPHealthSession]]


class InMemoryMCPHealthStore:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], MCPHealthRecord] = {}

    async def save(self, record: MCPHealthRecord) -> None:
        self._records[(record.tenant_id, record.server_id)] = record

    async def get(self, tenant_id: str, server_id: str) -> MCPHealthRecord | None:
        return self._records.get((tenant_id, server_id))


class PostgresMCPHealthStore:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    async def save(self, record: MCPHealthRecord) -> None:
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL storage") from exc
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_mcp_health_checks
                   (tenant_id,server_id,status,checked_at,latency_ms,
                    expected_tools,discovered_tools,error_code)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (tenant_id,server_id) DO UPDATE SET
                     status=EXCLUDED.status, checked_at=EXCLUDED.checked_at,
                     latency_ms=EXCLUDED.latency_ms,
                     expected_tools=EXCLUDED.expected_tools,
                     discovered_tools=EXCLUDED.discovered_tools,
                     error_code=EXCLUDED.error_code""",
                (record.tenant_id, record.server_id, record.status, record.checked_at,
                 record.latency_ms, Jsonb(list(record.expected_tools)),
                 Jsonb(list(record.discovered_tools)), record.error_code),
            )

    async def get(self, tenant_id: str, server_id: str) -> MCPHealthRecord | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT tenant_id,server_id,status,checked_at,latency_ms,
                          expected_tools,discovered_tools,error_code
                   FROM platform_mcp_health_checks
                   WHERE tenant_id=%s AND server_id=%s""",
                (tenant_id, server_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return MCPHealthRecord(
            tenant_id=row["tenant_id"], server_id=row["server_id"],
            status=row["status"], checked_at=row["checked_at"],
            latency_ms=int(row["latency_ms"]),
            expected_tools=tuple(row["expected_tools"] or ()),
            discovered_tools=tuple(row["discovered_tools"] or ()),
            error_code=row["error_code"],
        )


class MCPHealthChecker:
    """Ping a trusted MCP session and compare its tools to the registry allowlist."""

    def __init__(
        self, *, store: MCPHealthStore, session_factory: SessionFactory,
        timeout_seconds: float = 8,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.store = store
        self.session_factory = session_factory
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _tool_names(result: Any) -> tuple[str, ...]:
        tools = getattr(result, "tools", result)
        if not isinstance(tools, (list, tuple)):
            raise MCPHealthError("invalid MCP tool listing")
        names = tuple(sorted(
            str(getattr(tool, "name", "") or "") for tool in tools
        ))
        if any(not name for name in names) or len(names) != len(set(names)):
            raise MCPHealthError("MCP tool listing contains invalid or duplicate names")
        return names

    async def check(
        self, *, tenant_id: str, server_id: str,
        expected_tools: tuple[str, ...],
    ) -> MCPHealthRecord:
        if not tenant_id.strip() or not server_id.strip():
            raise ValueError("tenant_id and server_id are required")
        expected = tuple(sorted(set(expected_tools)))
        if not expected or any(not name.strip() for name in expected):
            raise ValueError("expected_tools must contain unique non-empty names")
        started = time.monotonic()
        discovered: tuple[str, ...] = ()
        status = "unhealthy"
        error_code: str | None = None
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with self.session_factory(tenant_id, server_id) as session:
                    await session.ping()
                    discovered = self._tool_names(await session.list_tools())
            if discovered == expected:
                status = "healthy"
            else:
                error_code = "tool_inventory_mismatch"
        except TimeoutError:
            error_code = "probe_timeout"
        except Exception as exc:
            # Persist only a stable class code, never exception text that may
            # contain endpoint details, headers, or credentials.
            error_code = "probe_" + type(exc).__name__[:64]
        record = MCPHealthRecord(
            tenant_id=tenant_id, server_id=server_id, status=status,
            checked_at=datetime.now(timezone.utc),
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
            expected_tools=expected, discovered_tools=discovered,
            error_code=error_code,
        )
        await self.store.save(record)
        return record
