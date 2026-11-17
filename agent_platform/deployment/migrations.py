"""Expand/contract database migration orchestration.

This module owns migration policy and state transitions, not SQL execution.
Infrastructure adapters supply idempotent phase handlers and transactional
writers. State is explicit so deployments can pause or roll back safely.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Mapping
from uuid import uuid4


class MigrationPhase(str, Enum):
    PLANNED = "planned"
    EXPANDED = "expanded"
    DUAL_WRITE = "dual_write"
    BACKFILL = "backfill"
    VALIDATED = "validated"
    CUTOVER = "cutover"
    CONTRACTED = "contracted"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class MigrationError(RuntimeError):
    """Migration cannot safely proceed."""


@dataclass(frozen=True)
class SchemaCompatibility:
    schema_version: int
    min_reader_version: int
    max_reader_version: int
    min_writer_version: int
    max_writer_version: int

    def __post_init__(self) -> None:
        if min(self.schema_version, self.min_reader_version, self.min_writer_version) < 1:
            raise ValueError("schema and compatibility versions must be positive")
        if self.max_reader_version < self.min_reader_version:
            raise ValueError("invalid reader compatibility range")
        if self.max_writer_version < self.min_writer_version:
            raise ValueError("invalid writer compatibility range")

    def supports(self, *, reader_version: int, writer_version: int) -> bool:
        return (self.min_reader_version <= reader_version <= self.max_reader_version
                and self.min_writer_version <= writer_version <= self.max_writer_version)


@dataclass(frozen=True)
class MigrationPlan:
    name: str
    from_schema: int
    to_schema: int
    old_app_version: int
    new_app_version: int
    compatibility: SchemaCompatibility
    rollback_compatibility: SchemaCompatibility
    migration_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("migration name must not be empty")
        if self.from_schema < 1 or self.to_schema <= self.from_schema:
            raise ValueError("migration schema versions must increase")
        if self.old_app_version < 1 or self.new_app_version <= self.old_app_version:
            raise ValueError("application versions must increase")
        if self.compatibility.schema_version != self.to_schema:
            raise ValueError("forward compatibility must describe target schema")
        if self.rollback_compatibility.schema_version != self.to_schema:
            raise ValueError("rollback compatibility must describe target schema")
        if not self.compatibility.supports(
            reader_version=self.old_app_version, writer_version=self.old_app_version
        ):
            raise ValueError("old application must remain compatible during expand/dual-write")
        if not self.compatibility.supports(
            reader_version=self.new_app_version, writer_version=self.new_app_version
        ):
            raise ValueError("new application must support expanded target schema")
        if not self.rollback_compatibility.supports(
            reader_version=self.old_app_version, writer_version=self.old_app_version
        ):
            raise ValueError("rollback schema must support the previous application")


@dataclass
class MigrationRun:
    plan: MigrationPlan
    phase: MigrationPhase = MigrationPhase.PLANNED
    dual_write_enabled: bool = False
    reads_on_new_schema: bool = False
    old_app_rollback_available: bool = True
    validation_digest: str | None = None
    failure_reason: str | None = None
    history: list[dict[str, str]] = field(default_factory=list)

    def advance(self, target: MigrationPhase, *, validation_digest: str | None = None) -> None:
        allowed = {
            MigrationPhase.PLANNED: MigrationPhase.EXPANDED,
            MigrationPhase.EXPANDED: MigrationPhase.DUAL_WRITE,
            MigrationPhase.DUAL_WRITE: MigrationPhase.BACKFILL,
            MigrationPhase.BACKFILL: MigrationPhase.VALIDATED,
            MigrationPhase.VALIDATED: MigrationPhase.CUTOVER,
            MigrationPhase.CUTOVER: MigrationPhase.CONTRACTED,
        }
        if allowed.get(self.phase) != target:
            raise MigrationError(f"invalid migration transition: {self.phase.value} -> {target.value}")
        if target == MigrationPhase.DUAL_WRITE:
            self.dual_write_enabled = True
        if target == MigrationPhase.VALIDATED and not validation_digest:
            raise MigrationError("validation digest is required before cutover")
        if target == MigrationPhase.VALIDATED:
            self.validation_digest = validation_digest
        if target == MigrationPhase.CUTOVER:
            if not self.dual_write_enabled or not self.validation_digest:
                raise MigrationError("dual-write and successful validation are required before cutover")
            self.reads_on_new_schema = True
        if target == MigrationPhase.CONTRACTED:
            self.old_app_rollback_available = False
            self.dual_write_enabled = False
        self.phase = target
        self.history.append({"phase": target.value})

    def rollback(self, reason: str) -> None:
        if self.phase in (MigrationPhase.CONTRACTED, MigrationPhase.ROLLED_BACK):
            raise MigrationError("rollback unavailable after contract phase")
        if not self.old_app_rollback_available:
            raise MigrationError("previous application version is no longer supported")
        if self.phase == MigrationPhase.PLANNED:
            raise MigrationError("migration has not started")
        self.phase = MigrationPhase.ROLLED_BACK
        self.dual_write_enabled = False
        self.reads_on_new_schema = False
        self.failure_reason = reason.strip() or "operator_requested"
        self.history.append({"phase": MigrationPhase.ROLLED_BACK.value,
                             "reason": self.failure_reason})

    def fail(self, reason: str) -> None:
        if self.phase in (MigrationPhase.CONTRACTED, MigrationPhase.ROLLED_BACK):
            raise MigrationError("terminal migration cannot be failed")
        self.phase = MigrationPhase.FAILED
        self.failure_reason = reason.strip() or "unspecified"
        self.history.append({"phase": MigrationPhase.FAILED.value,
                             "reason": self.failure_reason})


@dataclass(frozen=True)
class DualWriteResult:
    primary_succeeded: bool
    shadow_succeeded: bool
    shadow_error: str | None = None
    reconciliation_key: str | None = None


class DualWriteCoordinator:
    """Primary-first dual-write with durable reconciliation callback.

    A failed primary write is always surfaced. A failed shadow write is
    recorded for retry and never silently treated as success.
    """

    def __init__(self, *, primary: Callable[[Mapping[str, object]], None],
                 shadow: Callable[[Mapping[str, object]], None],
                 enqueue_reconciliation: Callable[[Mapping[str, object], str], str]):
        self.primary = primary
        self.shadow = shadow
        self.enqueue_reconciliation = enqueue_reconciliation

    def write(self, record: Mapping[str, object]) -> DualWriteResult:
        payload = dict(record)
        self.primary(payload)
        try:
            self.shadow(payload)
            return DualWriteResult(True, True)
        except Exception as exc:
            key = self.enqueue_reconciliation(payload, str(exc))
            return DualWriteResult(True, False, str(exc), key)


def verify_compatibility(*, schema: SchemaCompatibility, app_reader: int,
                         app_writer: int) -> None:
    if not schema.supports(reader_version=app_reader, writer_version=app_writer):
        raise MigrationError(
            f"application reader/writer {app_reader}/{app_writer} is incompatible "
            f"with schema {schema.schema_version}"
        )
