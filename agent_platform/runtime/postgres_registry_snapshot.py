"""PostgreSQL persistence for immutable per-Run registry snapshots."""
from __future__ import annotations

from typing import Any, Callable

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
                    """SELECT registry_snapshot,registry_snapshot_digest
                       FROM platform_runs WHERE tenant_id=%s AND run_id=%s FOR UPDATE""",
                    (snapshot.tenant_id, snapshot.run_id),
                )
                row = await cur.fetchone()
                if row is None:
                    raise KeyError("Run not found")
                existing = row["registry_snapshot"]
                pinned_entries = existing if isinstance(existing, list) else ()
                # A task submission stores the version references first; treat that
                # placeholder as unpinned so the authoritative snapshot can replace it.
                already_pinned = bool(pinned_entries) and all(
                    isinstance(entry, dict) and "definition" in entry
                    for entry in pinned_entries
                ) and bool(row["registry_snapshot_digest"])
                if already_pinned:
                    if row["registry_snapshot_digest"] != snapshot.digest:
                        raise ValueError("Run registry snapshot is immutable")
                    return RunRegistrySnapshot(
                        tenant_id=snapshot.tenant_id, run_id=snapshot.run_id,
                        entries=tuple(dict(entry) for entry in pinned_entries),
                        digest=row["registry_snapshot_digest"],
                    )
                await db.execute(
                    """UPDATE platform_runs
                       SET registry_snapshot=%s,registry_snapshot_digest=%s,updated_at=now()
                       WHERE tenant_id=%s AND run_id=%s""",
                    (Jsonb([dict(entry) for entry in snapshot.entries]), snapshot.digest,
                     snapshot.tenant_id, snapshot.run_id),
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
