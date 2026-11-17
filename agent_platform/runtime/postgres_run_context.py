"""PostgreSQL append-only store for versioned Run context snapshots."""
from __future__ import annotations

from typing import Any, Callable

from agent_platform.runtime.run_context import RunContextSnapshot


class PostgresRunContextSnapshotStore:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    @staticmethod
    def _snapshot(row: Any) -> RunContextSnapshot:
        return RunContextSnapshot(
            tenant_id=row["tenant_id"], run_id=str(row["run_id"]),
            revision=row["revision"], snapshot_id=str(row["snapshot_id"]),
            context=dict(row["context"]),
            references=tuple(dict(item) for item in row["reference_window"]),
            digest=row["digest"],
        )

    async def append(self, snapshot: RunContextSnapshot) -> RunContextSnapshot:
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL storage") from exc
        db = await self._connect()
        async with db:
            async with db.transaction():
                # Serialize revisions per Run; the Run row is the stable lock.
                cur = await db.execute(
                    "SELECT run_id FROM platform_runs "
                    "WHERE tenant_id=%s AND run_id=%s FOR UPDATE",
                    (snapshot.tenant_id, snapshot.run_id),
                )
                if await cur.fetchone() is None:
                    raise KeyError("Run not found")
                cur = await db.execute(
                    """SELECT revision FROM platform_run_context_snapshots
                       WHERE tenant_id=%s AND run_id=%s
                       ORDER BY revision DESC LIMIT 1""",
                    (snapshot.tenant_id, snapshot.run_id),
                )
                row = await cur.fetchone()
                expected = int(row["revision"]) + 1 if row else 1
                if snapshot.revision != expected:
                    raise ValueError("Run context snapshot revision conflict")
                await db.execute(
                    """INSERT INTO platform_run_context_snapshots
                       (tenant_id,run_id,revision,snapshot_id,context,reference_window,digest)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (snapshot.tenant_id, snapshot.run_id, snapshot.revision,
                     snapshot.snapshot_id, Jsonb(dict(snapshot.context)),
                     Jsonb([dict(item) for item in snapshot.references]), snapshot.digest),
                )
        return snapshot

    async def latest(self, tenant_id: str, run_id: str) -> RunContextSnapshot | None:
        db = await self._connect()
        async with db:
            cur = await db.execute(
                """SELECT tenant_id,run_id,revision,snapshot_id,context,reference_window,digest
                   FROM platform_run_context_snapshots
                   WHERE tenant_id=%s AND run_id=%s ORDER BY revision DESC LIMIT 1""",
                (tenant_id, run_id),
            )
            row = await cur.fetchone()
        return self._snapshot(row) if row else None
