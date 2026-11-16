"""Resumable, tenant-scoped orchestration for cross-domain subject exports."""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import AsyncIterable, Mapping, Protocol, Sequence
from uuid import UUID


class SubjectExportAdapter(Protocol):
    """A domain adapter must enforce its own tenant and subject ownership filters."""

    def export_subject(
        self, *, tenant_id: str, subject_id: str,
    ) -> AsyncIterable[Mapping[str, object]]: ...


class SubjectExportSink(Protocol):
    """Durable sink; writes must be idempotent for operation/source/ordinal."""

    async def write_record(
        self, *, operation_id: str, source: str, ordinal: int, record: bytes,
    ) -> None: ...


@dataclass(frozen=True)
class ExportSourceResult:
    source: str
    record_count: int
    sha256: str


@dataclass(frozen=True)
class SubjectExportResult:
    operation_id: str
    subject_id: str
    completed: tuple[str, ...]
    failed: Mapping[str, str]
    sources: tuple[ExportSourceResult, ...]

    @property
    def complete(self) -> bool:
        return not self.failed


def _json_default(value: object) -> object:
    """Encode common PostgreSQL scalar types without lossy implicit repr()."""
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes):
        return {"$binary_base64": base64.b64encode(value).decode("ascii")}
    raise TypeError(f"unsupported export value type: {type(value).__name__}")


class SubjectExportCoordinator:
    """Stream export records without buffering full tenant datasets in memory.

    completed_sources must come from a durable operation ledger. The sink
    should use the documented idempotency tuple to tolerate retries after a
    worker crash between writing records and recording source completion.
    """

    def __init__(self, adapters: Mapping[str, SubjectExportAdapter]) -> None:
        if not adapters or any(not name.strip() for name in adapters):
            raise ValueError("at least one named subject export adapter is required")
        self.adapters = dict(adapters)

    async def export(
        self,
        *,
        tenant_id: str,
        subject_id: str,
        operation_id: str,
        sink: SubjectExportSink,
        completed_sources: Sequence[str] = (),
    ) -> SubjectExportResult:
        if not tenant_id or not subject_id or not operation_id:
            raise ValueError("tenant, subject and operation ids are required")
        completed_set = set(completed_sources)
        unknown = completed_set - self.adapters.keys()
        if unknown:
            raise ValueError("completed sources include unknown adapters")

        completed: list[str] = [
            name for name in self.adapters if name in completed_set
        ]
        failed: dict[str, str] = {}
        source_results: list[ExportSourceResult] = []
        for name, adapter in self.adapters.items():
            if name in completed_set:
                continue
            digest = hashlib.sha256()
            count = 0
            try:
                async for item in adapter.export_subject(
                    tenant_id=tenant_id, subject_id=subject_id,
                ):
                    if not isinstance(item, Mapping):
                        raise TypeError("export adapter must yield mapping records")
                    encoded = json.dumps(
                        dict(item), ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"), allow_nan=False,
                        default=_json_default,
                    ).encode("utf-8")
                    digest.update(encoded)
                    digest.update(b"\n")
                    await sink.write_record(
                        operation_id=operation_id, source=name,
                        ordinal=count, record=encoded,
                    )
                    count += 1
                completed.append(name)
                source_results.append(ExportSourceResult(
                    source=name, record_count=count, sha256=digest.hexdigest(),
                ))
            except Exception as exc:
                failed[name] = type(exc).__name__
        return SubjectExportResult(
            operation_id=operation_id, subject_id=subject_id,
            completed=tuple(completed), failed=failed,
            sources=tuple(source_results),
        )
