"""PostgreSQL persistence for immutable per-Run registry snapshots."""
from __future__ import annotations

from typing import Any, Callable, Mapping

from agent_platform.runtime.registry_snapshot import RunRegistrySnapshot


class PostgresRunSnapshotStore:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    async def save_once(self, snapshot: RunRegistrySnapshot) -> RunRegistrySnapshot:
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL storage") from exc
        db = await self._connect()
        async with db:
            async with db.transaction():
                cur = await db.execute(
                    """UPDATE platform_runs
                       SET registry_snapshot=%s,registry_snapshot_digest=%s,updated_at=now()
                       WHERE tenant_id=%s AND run_id=%s AND registry_snapshot IS NULL
                       RETURNING run_id""",
                    (Jsonb([dict(entry) for entry in snapshot.entries]), snapshot.digest,
                     snapshot.tenant_id, snapshot.run_id),
                )
                inserted = await cur.fetchone()
                if inserted is None:
                    cur = await db.execute(
                        """SELECT registry_snapshot,registry_snapshot_digest
                           FROM platform_runs WHERE tenant_id=%s AND run_id=%s FOR UPDATE""",
                        (snapshot.tenant_id, snapshot.run_id),
                    )
                    row = await cur.fetchone()
                    if row is None:
                        raise KeyError("Run not found")
                    if row["registry_snapshot_digest"] != snapshot.digest:
                        raise ValueError("Run registry snapshot is immutable")
                    return RunRegistrySnapshot(
                        tenant_id=snapshot.tenant_id, run_id=snapshot.run_id,
                        entries=tuple(dict(entry) for entry in (row["registry_snapshot"] or ())),
                        digest=row["registry_snapshot_digest"],
                    )
        return snapshot

    async def get(self, tenant_id: str, run_id: str) -> RunRegistrySnapshot | None:
        db = await self._connect()
        async with db:
            cur = await db.execute(
                """SELECT registry_snapshot,registry_snapshot_digest
                   FROM platform_runs WHERE tenant_id=%s AND run_id=%s""",
                (tenant_id, run_id),
            )
            row = await cur.fetchone()
        if row is None or row["registry_snapshot"] is None:
            return None
        return RunRegistrySnapshot(
            tenant_id=tenant_id, run_id=run_id,
            entries=tuple(dict(entry) for entry in row["registry_snapshot"]),
            digest=row["registry_snapshot_digest"],
        )
