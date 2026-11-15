"""Explicit source-of-truth and deletion lifecycle contracts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class DataOwnership:
    object_type: str
    source_system: str
    target_system: str
    authoritative_writer: str
    migration_mode: str
    source_key: str
    target_key: str

    def __post_init__(self) -> None:
        if not all((self.object_type, self.source_system, self.target_system,
                    self.authoritative_writer, self.source_key, self.target_key)):
            raise ValueError("data ownership mapping fields are required")
        if self.migration_mode not in {"adapter", "batch", "rebuild", "retire"}:
            raise ValueError("unsupported migration mode")
        if self.authoritative_writer not in {self.source_system, self.target_system}:
            raise ValueError("authoritative writer must be source or target system")


@dataclass(frozen=True)
class RetentionPolicy:
    object_type: str
    retain_for: timedelta
    legal_hold_supported: bool = True
    soft_delete_days: int = 30

    def __post_init__(self) -> None:
        if not self.object_type or self.retain_for <= timedelta(0) or self.soft_delete_days < 0:
            raise ValueError("invalid retention policy")


@dataclass(frozen=True)
class DeletionResult:
    operation_id: str
    subject_id: str
    completed: tuple[str, ...]
    failed: Mapping[str, str]
    pending: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.failed and not self.pending


class DeletionAdapter(Protocol):
    async def delete_subject(self, *, tenant_id: str, subject_id: str,
                             operation_id: str) -> None: ...


class DeletionCoordinator:
    """Run a deletion across all configured stores; failures remain explicit and retryable."""

    def __init__(self, adapters: Mapping[str, DeletionAdapter]) -> None:
        if not adapters or any(not name.strip() for name in adapters):
            raise ValueError("at least one named deletion adapter is required")
        self.adapters = dict(adapters)

    async def delete(self, *, tenant_id: str, subject_id: str,
                     operation_id: str, completed_systems: Sequence[str] = ()) -> DeletionResult:
        if not tenant_id or not subject_id or not operation_id:
            raise ValueError("tenant, subject and operation ids are required")
        unknown = set(completed_systems) - self.adapters.keys()
        if unknown:
            raise ValueError("completed systems include unknown adapters")
        completed = list(dict.fromkeys(completed_systems))
        failed: dict[str, str] = {}
        for name, adapter in self.adapters.items():
            if name in completed:
                continue
            try:
                await adapter.delete_subject(
                    tenant_id=tenant_id, subject_id=subject_id,
                    operation_id=operation_id,
                )
                completed.append(name)
            except Exception as exc:
                failed[name] = type(exc).__name__
        pending = tuple(name for name in self.adapters if name not in completed and name not in failed)
        return DeletionResult(
            operation_id=operation_id, subject_id=subject_id,
            completed=tuple(completed), failed=failed, pending=pending,
        )


def migration_fingerprint(*, source_system: str, target_system: str,
                           source_key: str, payload: Mapping[str, Any]) -> str:
    """Stable idempotency key for a source row and its normalized payload."""
    canonical = json.dumps(
        {"source_system": source_system, "target_system": target_system,
         "source_key": source_key, "payload": payload},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
