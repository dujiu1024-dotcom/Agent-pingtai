"""Durable, tenant-scoped Run context snapshots and bounded reference windows.

This module deliberately does not estimate tokens or enforce model context-window
budgets; those policies belong to Issue #72.
"""

# isort: skip_file
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence
from uuid import uuid4


_CONTEXT_FIELDS = ("objective", "constraints", "decisions", "evidence", "pending_items")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _json_copy(value: Any) -> Any:
    return json.loads(_canonical(value))


@dataclass(frozen=True)
class RunContextSnapshot:
    tenant_id: str
    run_id: str
    revision: int
    snapshot_id: str
    context: Mapping[str, Any]
    references: tuple[Mapping[str, Any], ...]
    digest: str


class RunContextSnapshotStore(Protocol):
    async def append(self, snapshot: RunContextSnapshot) -> RunContextSnapshot: ...
    async def latest(self, tenant_id: str, run_id: str) -> RunContextSnapshot | None: ...


class InMemoryRunContextSnapshotStore:
    """Immutable append-only snapshots, isolated by tenant and Run."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], list[RunContextSnapshot]] = {}

    async def append(self, snapshot: RunContextSnapshot) -> RunContextSnapshot:
        key = (snapshot.tenant_id, snapshot.run_id)
        rows = self._rows.setdefault(key, [])
        if rows and snapshot.revision != rows[-1].revision + 1:
            raise ValueError("Run context snapshot revision conflict")
        if not rows and snapshot.revision != 1:
            raise ValueError("first Run context snapshot revision must be 1")
        rows.append(copy.deepcopy(snapshot))
        return copy.deepcopy(snapshot)

    async def latest(self, tenant_id: str, run_id: str) -> RunContextSnapshot | None:
        rows = self._rows.get((tenant_id, run_id), ())
        return copy.deepcopy(rows[-1]) if rows else None


class RunContextSnapshotService:
    """Assemble, persist and reconstruct the execution context for one Run."""

    def __init__(
        self, *, tenant_id: str, store: RunContextSnapshotStore,
        reference_window_size: int = 40,
    ) -> None:
        if not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not 1 <= reference_window_size <= 500:
            raise ValueError("reference_window_size must be between 1 and 500")
        self.tenant_id = tenant_id
        self.store = store
        self.reference_window_size = reference_window_size

    def _scope(self, tenant_id: str) -> None:
        if tenant_id != self.tenant_id:
            raise PermissionError("Run context tenant mismatch")

    @staticmethod
    def _digest(
        tenant_id: str, run_id: str, revision: int, context: Mapping[str, Any],
        references: Sequence[Mapping[str, Any]],
    ) -> str:
        body = {"tenant_id": tenant_id, "run_id": run_id, "revision": revision,
                "context": dict(context), "references": [dict(item) for item in references]}
        return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()

    @staticmethod
    def _reference(item: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(item, Mapping):
            raise ValueError("context references must be objects")
        ref_id = item.get("ref_id")
        ref_type = item.get("type", "evidence")
        if not isinstance(ref_id, str) or not ref_id.strip():
            raise ValueError("each context reference requires a non-empty ref_id")
        if not isinstance(ref_type, str) or not ref_type.strip():
            raise ValueError("context reference type must be a non-empty string")
        result = _json_copy(dict(item))
        result["ref_id"] = ref_id
        result["type"] = ref_type
        result["pinned"] = bool(item.get("pinned", False))
        return result

    def _window(
        self, existing: Sequence[Mapping[str, Any]],
        incoming: Sequence[Mapping[str, Any]],
    ) -> tuple[Mapping[str, Any], ...]:
        # Latest version wins for a repeated ref_id; preserve insertion order.
        by_id: dict[str, dict[str, Any]] = {}
        for item in (*existing, *incoming):
            normalized = self._reference(item)
            previous = by_id.get(normalized["ref_id"])
            if previous is not None and previous.get("pinned"):
                normalized["pinned"] = True
            by_id[normalized["ref_id"]] = normalized
        all_refs = list(by_id.values())
        pinned = [item for item in all_refs if item["pinned"]]
        if len(pinned) > self.reference_window_size:
            raise ValueError("pinned context references exceed the configured reference window")
        recent = [item for item in all_refs if not item["pinned"]]
        remaining = self.reference_window_size - len(pinned)
        selected = pinned + (recent[-remaining:] if remaining else [])
        return tuple(_json_copy(item) for item in selected)

    async def capture(
        self, *, tenant_id: str, run_id: str,
        initial_context: Mapping[str, Any] | None = None,
        updates: Mapping[str, Any] | None = None,
        references: Sequence[Mapping[str, Any]] = (),
    ) -> RunContextSnapshot:
        self._scope(tenant_id)
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id is required")
        if initial_context is not None and not isinstance(initial_context, Mapping):
            raise ValueError("initial_context must be an object")
        if updates is not None and not isinstance(updates, Mapping):
            raise ValueError("context updates must be an object")
        current = await self.store.latest(tenant_id, run_id)
        merged = dict(current.context) if current is not None else {}
        source = dict(initial_context or {})
        source.update(dict(updates or {}))
        # Retain the original request as immutable context input; the named fields
        # are first-class and may be augmented in later revisions.
        if current is None:
            merged["request"] = _json_copy(source)
        for field in _CONTEXT_FIELDS:
            if field in source:
                merged[field] = _json_copy(source[field])
            elif field not in merged:
                merged[field] = [] if field != "objective" else ""
        for key, value in source.items():
            if key not in _CONTEXT_FIELDS and key != "request":
                merged[key] = _json_copy(value)
        window = self._window(current.references if current else (), references)
        revision = current.revision + 1 if current else 1
        context = _json_copy(merged)
        digest = self._digest(tenant_id, run_id, revision, context, window)
        snapshot = RunContextSnapshot(
            tenant_id=tenant_id, run_id=run_id, revision=revision,
            snapshot_id=str(uuid4()), context=context, references=window, digest=digest,
        )
        return await self.store.append(snapshot)

    async def ensure_initial(
        self, *, tenant_id: str, run_id: str, initial_context: Mapping[str, Any],
    ) -> RunContextSnapshot:
        self._scope(tenant_id)
        current = await self.store.latest(tenant_id, run_id)
        if current is not None:
            return self._verify(current, tenant_id, run_id)
        return await self.capture(
            tenant_id=tenant_id, run_id=run_id, initial_context=initial_context,
        )

    def _verify(
        self, snapshot: RunContextSnapshot, tenant_id: str, run_id: str,
    ) -> RunContextSnapshot:
        if snapshot.tenant_id != tenant_id or snapshot.run_id != run_id:
            raise PermissionError("Run context snapshot scope mismatch")
        expected = self._digest(
            tenant_id, run_id, snapshot.revision, snapshot.context, snapshot.references,
        )
        if snapshot.digest != expected:
            raise ValueError("Run context snapshot integrity check failed")
        return snapshot

    async def rebuild(self, *, tenant_id: str, run_id: str) -> dict[str, Any]:
        self._scope(tenant_id)
        snapshot = await self.store.latest(tenant_id, run_id)
        if snapshot is None:
            raise KeyError("Run context snapshot is missing; recovery refused")
        self._verify(snapshot, tenant_id, run_id)
        # Return a fresh JSON-compatible object so graph nodes cannot mutate the
        # durable snapshot held by the store.
        return {
            **_json_copy(dict(snapshot.context)),
            "references": _json_copy(list(snapshot.references)),
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_revision": snapshot.revision,
            "snapshot_digest": snapshot.digest,
        }


class PostgresRunContextSnapshotStore:
    """Append-only PostgreSQL storage for tenant-scoped Run context snapshots."""

    def __init__(self, *, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    @classmethod
    def from_dsn(cls, *, dsn: str) -> "PostgresRunContextSnapshotStore":
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("PostgreSQL DSN is required")

        async def connect() -> Any:
            try:
                from psycopg import AsyncConnection
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError(
                    "Install psycopg[binary,pool] for PostgreSQL storage"
                ) from exc
            return await AsyncConnection.connect(dsn, row_factory=dict_row)

        return cls(connection_factory=connect)

    @staticmethod
    def _jsonb(value: Any) -> Any:
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError(
                "Install psycopg[binary,pool] for PostgreSQL storage"
            ) from exc
        return Jsonb(value)

    @staticmethod
    def _from_row(row: Mapping[str, Any] | None) -> RunContextSnapshot | None:
        if row is None:
            return None
        return RunContextSnapshot(
            tenant_id=str(row["tenant_id"]),
            run_id=str(row["run_id"]),
            revision=int(row["revision"]),
            snapshot_id=str(row["snapshot_id"]),
            context=_json_copy(row["context_payload"]),
            references=tuple(_json_copy(row["references_payload"] or [])),
            digest=str(row["digest"]),
        )

    async def append(self, snapshot: RunContextSnapshot) -> RunContextSnapshot:
        if not snapshot.tenant_id.strip() or not snapshot.run_id.strip():
            raise ValueError("snapshot tenant_id and run_id are required")
        expected_digest = RunContextSnapshotService._digest(
            snapshot.tenant_id, snapshot.run_id, snapshot.revision,
            snapshot.context, snapshot.references,
        )
        if snapshot.digest != expected_digest:
            raise ValueError("Run context snapshot integrity check failed")
        try:
            from uuid import UUID
            run_uuid = UUID(snapshot.run_id)
            snapshot_uuid = UUID(snapshot.snapshot_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("PostgreSQL Run context IDs must be UUIDs") from exc

        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                # Serialize revision allocation per tenant/Run across worker processes.
                lock_key = f"{snapshot.tenant_id}:{run_uuid}"
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (lock_key,),
                )
                cursor = await connection.execute(
                    """SELECT revision FROM platform_run_context_snapshots
                       WHERE tenant_id = %s AND run_id = %s
                       ORDER BY revision DESC LIMIT 1""",
                    (snapshot.tenant_id, run_uuid),
                )
                current = await cursor.fetchone()
                expected_revision = int(current["revision"]) + 1 if current else 1
                if snapshot.revision != expected_revision:
                    raise ValueError("Run context snapshot revision conflict")
                await connection.execute(
                    """INSERT INTO platform_run_context_snapshots
                       (tenant_id, run_id, revision, snapshot_id, context_payload,
                        references_payload, digest)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (
                        snapshot.tenant_id, run_uuid, snapshot.revision, snapshot_uuid,
                        self._jsonb(_json_copy(snapshot.context)),
                        self._jsonb(_json_copy(list(snapshot.references))),
                        snapshot.digest,
                    ),
                )
        return RunContextSnapshot(
            tenant_id=snapshot.tenant_id, run_id=snapshot.run_id,
            revision=snapshot.revision, snapshot_id=snapshot.snapshot_id,
            context=_json_copy(snapshot.context),
            references=tuple(_json_copy(list(snapshot.references))),
            digest=snapshot.digest,
        )

    async def latest(self, tenant_id: str, run_id: str) -> RunContextSnapshot | None:
        if not tenant_id.strip() or not run_id.strip():
            raise ValueError("tenant_id and run_id are required")
        try:
            from uuid import UUID
            run_uuid = UUID(run_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("PostgreSQL Run context IDs must be UUIDs") from exc
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """SELECT tenant_id, run_id, revision, snapshot_id,
                          context_payload, references_payload, digest
                   FROM platform_run_context_snapshots
                   WHERE tenant_id = %s AND run_id = %s
                   ORDER BY revision DESC LIMIT 1""",
                (tenant_id, run_uuid),
            )
            row = await cursor.fetchone()
        return self._from_row(row)
