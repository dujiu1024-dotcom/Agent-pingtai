"""Fail-closed orchestration for the legacy-system rollback procedure.

The host supplies production-specific write fencing, PostgreSQL-to-SQLite
backfill, parity verification, and gateway switching operations. This module
orders those operations and enforces a single-process deadline; it does not
pretend to provide a durable distributed lock or perform database I/O itself.
"""
from __future__ import annotations

import asyncio
import math
import re
import time
from dataclasses import dataclass
from typing import Awaitable, Callable


RollbackOperation = Callable[..., Awaitable[object]]
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class RollbackDataReport:
    """Evidence emitted by the host's PostgreSQL-to-SQLite backfill."""

    source_tables: tuple[str, ...]
    target_tables: tuple[str, ...]
    source_rows: int
    target_rows: int
    source_sha256: str
    target_sha256: str
    excluded_tables: tuple[str, ...] = ()
    unmapped_tables: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, tables in (
            ("source_tables", self.source_tables),
            ("target_tables", self.target_tables),
            ("excluded_tables", self.excluded_tables),
            ("unmapped_tables", self.unmapped_tables),
        ):
            if not isinstance(tables, tuple) or any(
                not isinstance(table, str) or not table.strip() for table in tables
            ):
                raise ValueError(f"{name} must be a tuple of non-empty table names")
            if len(tables) != len(set(tables)):
                raise ValueError(f"{name} must not contain duplicate table names")
        for name, value in (("source_rows", self.source_rows), ("target_rows", self.target_rows)):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name, value in (
            ("source_sha256", self.source_sha256),
            ("target_sha256", self.target_sha256),
        ):
            if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")

    @property
    def parity_verified(self) -> bool:
        """True only when every table and row is represented identically."""
        return (
            bool(self.source_tables)
            and set(self.source_tables) == set(self.target_tables)
            and self.source_rows == self.target_rows
            and self.source_sha256 == self.target_sha256
            and not self.excluded_tables
            and not self.unmapped_tables
        )


@dataclass(frozen=True)
class RollbackResult:
    rollback_id: str
    data_report: RollbackDataReport
    elapsed_seconds: float
    gateway_switched: bool = True


class RollbackExecutionError(RuntimeError):
    """A rollback step failed; gateway_switched indicates whether traffic moved."""

    def __init__(self, stage: str, gateway_switched: bool, reason: str) -> None:
        self.stage = stage
        self.gateway_switched = gateway_switched
        self.reason = reason
        super().__init__(
            f"rollback failed at {stage} (gateway_switched={gateway_switched}): {reason}"
        )


class RollbackCoordinator:
    """Run the rollback sequence once, under a strict end-to-end time budget.

    The required order is: fence/drain platform writes, backfill PostgreSQL to
    SQLite, verify complete parity, switch the gateway to legacy, then resume
    legacy writes. Any failure before the gateway switch leaves the platform
    write fence in place so operators cannot create divergent writes.
    """

    def __init__(
        self,
        *,
        quiesce_platform_writes: RollbackOperation,
        backfill_postgres_to_sqlite: RollbackOperation,
        verify_legacy_parity: RollbackOperation,
        switch_gateway_to_legacy: RollbackOperation,
        resume_legacy_writes: RollbackOperation,
        timeout_seconds: float = 300.0,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 300
        ):
            raise ValueError("timeout_seconds must be in (0, 300]")
        operations = {
            "quiesce_platform_writes": quiesce_platform_writes,
            "backfill_postgres_to_sqlite": backfill_postgres_to_sqlite,
            "verify_legacy_parity": verify_legacy_parity,
            "switch_gateway_to_legacy": switch_gateway_to_legacy,
            "resume_legacy_writes": resume_legacy_writes,
        }
        if any(not callable(operation) for operation in operations.values()):
            raise ValueError("all rollback operations must be callable")
        self._operations = operations
        self.timeout_seconds = float(timeout_seconds)
        self._lock = asyncio.Lock()

    async def execute(self, rollback_id: str) -> RollbackResult:
        if not isinstance(rollback_id, str) or not rollback_id.strip():
            raise ValueError("rollback_id is required")
        started = time.monotonic()
        deadline = started + self.timeout_seconds
        try:
            await asyncio.wait_for(
                self._lock.acquire(), timeout=max(0.0, deadline - time.monotonic()),
            )
        except asyncio.TimeoutError as exc:
            raise RollbackExecutionError("lock", False, "rollback deadline exceeded") from exc

        stage = "quiesce_platform_writes"
        gateway_switched = False
        try:
            await self._run(stage, rollback_id, deadline=deadline)
            stage = "backfill_postgres_to_sqlite"
            report = await self._run(stage, rollback_id, deadline=deadline)
            if not isinstance(report, RollbackDataReport):
                raise RollbackExecutionError(stage, False, "backfill returned no parity report")
            if not report.parity_verified:
                raise RollbackExecutionError(
                    "backfill_validation", False,
                    "table inventory, row counts, or full-export checksums do not match",
                )
            stage = "verify_legacy_parity"
            verified = await self._run(stage, rollback_id, report, deadline=deadline)
            if verified is not True:
                raise RollbackExecutionError(stage, False, "host parity verification rejected")
            stage = "switch_gateway_to_legacy"
            await self._run(stage, rollback_id, deadline=deadline)
            gateway_switched = True
            stage = "resume_legacy_writes"
            await self._run(stage, rollback_id, deadline=deadline, gateway_switched=True)
            return RollbackResult(
                rollback_id=rollback_id,
                data_report=report,
                elapsed_seconds=time.monotonic() - started,
            )
        except RollbackExecutionError:
            raise
        except Exception as exc:
            raise RollbackExecutionError(stage, gateway_switched, type(exc).__name__) from exc
        finally:
            self._lock.release()

    async def _run(
        self, stage: str, rollback_id: str, *args: object, deadline: float,
        gateway_switched: bool = False,
    ) -> object:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RollbackExecutionError(stage, gateway_switched, "rollback deadline exceeded")
        operation = self._operations[stage]
        try:
            return await asyncio.wait_for(operation(rollback_id, *args), timeout=remaining)
        except asyncio.TimeoutError as exc:
            raise RollbackExecutionError(stage, gateway_switched, "rollback deadline exceeded") from exc
        except RollbackExecutionError:
            raise
        except Exception as exc:
            raise RollbackExecutionError(stage, gateway_switched, type(exc).__name__) from exc
