"""Guarded dual-run, canary cutover, rollback and legacy retirement policy.

This module is deliberately side-effect free: runtime adapters consume the
routing decisions and persist evidence in their own durable control plane.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from enum import StrEnum


class CutoverPhase(StrEnum):
    LEGACY_PRIMARY = "legacy_primary"
    DUAL_WRITE = "dual_write"
    CANARY = "canary"
    TARGET_PRIMARY = "target_primary"
    ROLLED_BACK = "rolled_back"
    RETIRED = "retired"


@dataclass(frozen=True)
class ConsistencyEvidence:
    sampled_rows: int
    mismatched_rows: int
    source_checksum: str
    target_checksum: str
    replication_lag_seconds: float
    write_error_rate: float = 0.0
    observed_seconds: float = 0.0
    legacy_write_rate: float = 0.0
    evidence_age_seconds: float = 0.0

    def __post_init__(self) -> None:
        for name, value in (("sampled_rows", self.sampled_rows),
                            ("mismatched_rows", self.mismatched_rows)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.mismatched_rows > self.sampled_rows:
            raise ValueError("mismatched_rows cannot exceed sampled_rows")
        if not self.source_checksum.strip() or not self.target_checksum.strip():
            raise ValueError("source and target checksums are required")
        for name, value in (("replication_lag_seconds", self.replication_lag_seconds),
                            ("write_error_rate", self.write_error_rate),
                            ("observed_seconds", self.observed_seconds),
                            ("legacy_write_rate", self.legacy_write_rate),
                            ("evidence_age_seconds", self.evidence_age_seconds)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite non-negative number")
        if self.write_error_rate > 1 or self.legacy_write_rate > 1:
            raise ValueError("error and write rates must be between 0 and 1")

    @property
    def mismatch_rate(self) -> float:
        return self.mismatched_rows / self.sampled_rows if self.sampled_rows else 1.0

    @property
    def checksums_match(self) -> bool:
        return self.source_checksum == self.target_checksum


@dataclass(frozen=True)
class CutoverPolicy:
    max_mismatch_rate: float = 0.0
    max_replication_lag_seconds: float = 5.0
    max_write_error_rate: float = 0.001
    min_sampled_rows: int = 100
    min_observation_seconds: float = 900.0
    rollback_window_seconds: float = 86400.0
    max_evidence_age_seconds: float = 60.0

    def __post_init__(self) -> None:
        for name, value in (("max_mismatch_rate", self.max_mismatch_rate),
                            ("max_write_error_rate", self.max_write_error_rate)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        for name, value in (("max_replication_lag_seconds", self.max_replication_lag_seconds),
                            ("min_observation_seconds", self.min_observation_seconds),
                            ("rollback_window_seconds", self.rollback_window_seconds),
                            ("max_evidence_age_seconds", self.max_evidence_age_seconds)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if isinstance(self.min_sampled_rows, bool) or not isinstance(self.min_sampled_rows, int) or self.min_sampled_rows < 1:
            raise ValueError("min_sampled_rows must be a positive integer")


@dataclass(frozen=True)
class CutoverDecision:
    phase: CutoverPhase
    allowed: bool
    rollback_required: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class WritePlan:
    authoritative_system: str
    write_systems: tuple[str, ...]
    replication_targets: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReadPlan:
    primary_system: str
    shadow_system: str | None


def evaluate_consistency(evidence: ConsistencyEvidence, policy: CutoverPolicy) -> tuple[str, ...]:
    failures: list[str] = []
    if evidence.evidence_age_seconds > policy.max_evidence_age_seconds:
        failures.append("evidence_stale")
    if evidence.sampled_rows < policy.min_sampled_rows:
        failures.append("insufficient_sample")
    if evidence.mismatched_rows and evidence.mismatch_rate > policy.max_mismatch_rate:
        failures.append("row_mismatch_threshold_exceeded")
    if not evidence.checksums_match:
        failures.append("checksum_mismatch")
    if evidence.replication_lag_seconds > policy.max_replication_lag_seconds:
        failures.append("replication_lag_threshold_exceeded")
    if evidence.write_error_rate > policy.max_write_error_rate:
        failures.append("write_error_threshold_exceeded")
    return tuple(failures)


def choose_read_plan(*, phase: CutoverPhase, tenant_id: str, request_key: str,
                     canary_percent: int = 0) -> ReadPlan:
    if not tenant_id.strip() or not request_key.strip():
        raise ValueError("tenant_id and request_key are required")
    if isinstance(canary_percent, bool) or not isinstance(canary_percent, int) or not 0 <= canary_percent <= 100:
        raise ValueError("canary_percent must be an integer from 0 to 100")
    if phase in {CutoverPhase.LEGACY_PRIMARY, CutoverPhase.DUAL_WRITE, CutoverPhase.ROLLED_BACK}:
        return ReadPlan("legacy", "target" if phase == CutoverPhase.DUAL_WRITE else None)
    if phase == CutoverPhase.RETIRED:
        return ReadPlan("target", None)
    if phase == CutoverPhase.TARGET_PRIMARY:
        return ReadPlan("target", "legacy")
    digest = hashlib.sha256(f"{tenant_id}:{request_key}".encode()).digest()
    bucket = int.from_bytes(digest[:8], "big") % 100
    return ReadPlan("target" if bucket < canary_percent else "legacy",
                    "legacy" if bucket < canary_percent else "target")


def plan_write(*, phase: CutoverPhase) -> WritePlan:
    if phase in {CutoverPhase.LEGACY_PRIMARY, CutoverPhase.ROLLED_BACK}:
        return WritePlan("legacy", ("legacy",))
    if phase == CutoverPhase.DUAL_WRITE:
        return WritePlan("legacy", ("legacy",), ("target",))
    if phase in {CutoverPhase.CANARY, CutoverPhase.TARGET_PRIMARY}:
        return WritePlan("target", ("target",), ("legacy",))
    if phase == CutoverPhase.RETIRED:
        return WritePlan("target", ("target",))
    raise ValueError("unsupported cutover phase")


def evaluate_cutover(*, current: CutoverPhase, requested: CutoverPhase,
                     evidence: ConsistencyEvidence, policy: CutoverPolicy) -> CutoverDecision:
    """Validate phase advancement; unsafe live phases fail closed to rollback."""
    failures = evaluate_consistency(evidence, policy)
    if current in {CutoverPhase.DUAL_WRITE, CutoverPhase.CANARY, CutoverPhase.TARGET_PRIMARY} and failures:
        return CutoverDecision(CutoverPhase.ROLLED_BACK, False, True, failures)
    allowed_edges = {
        CutoverPhase.LEGACY_PRIMARY: {CutoverPhase.DUAL_WRITE},
        CutoverPhase.DUAL_WRITE: {CutoverPhase.CANARY, CutoverPhase.ROLLED_BACK},
        CutoverPhase.CANARY: {CutoverPhase.CANARY, CutoverPhase.TARGET_PRIMARY, CutoverPhase.ROLLED_BACK},
        CutoverPhase.TARGET_PRIMARY: {CutoverPhase.RETIRED, CutoverPhase.ROLLED_BACK},
        CutoverPhase.ROLLED_BACK: {CutoverPhase.DUAL_WRITE},
        CutoverPhase.RETIRED: set(),
    }
    if requested not in allowed_edges[current]:
        return CutoverDecision(current, False, False, ("invalid_phase_transition",))
    if requested in {CutoverPhase.DUAL_WRITE, CutoverPhase.CANARY, CutoverPhase.TARGET_PRIMARY} and failures:
        return CutoverDecision(current, False, False, failures)
    if requested == CutoverPhase.CANARY and evidence.observed_seconds < policy.min_observation_seconds:
        return CutoverDecision(current, False, False, ("observation_window_not_met",))
    if requested == CutoverPhase.TARGET_PRIMARY and evidence.observed_seconds < policy.min_observation_seconds:
        return CutoverDecision(current, False, False, ("observation_window_not_met",))
    if requested == CutoverPhase.RETIRED:
        retirement_failures = []
        if evidence.legacy_write_rate != 0:
            retirement_failures.append("legacy_writes_still_observed")
        if evidence.observed_seconds < policy.rollback_window_seconds:
            retirement_failures.append("rollback_window_not_elapsed")
        if failures:
            retirement_failures.extend(failures)
        if retirement_failures:
            return CutoverDecision(current, False, False, tuple(retirement_failures))
    return CutoverDecision(requested, True, False, ())


def rollback_required(*, evidence: ConsistencyEvidence, policy: CutoverPolicy) -> bool:
    """Return true for failed consistency, stale evidence, lag, or write-error guardrails."""
    return bool(evaluate_consistency(evidence, policy))
