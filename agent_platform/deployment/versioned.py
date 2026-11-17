"""Versioned deployment planning and guarded rollout state machine.

The module is provider-neutral: adapters perform traffic switching and health
probes, while this layer validates rollout policy and decides progression.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from enum import Enum
from typing import Iterable
from uuid import uuid4


class DeploymentStrategy(str, Enum):
    ROLLING = "rolling"
    BLUE_GREEN = "blue_green"
    CANARY = "canary"
    GRAY = "gray"


class DeploymentState(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


@dataclass(frozen=True)
class MaintenanceWindow:
    """Weekly local-time maintenance window; end is exclusive.

    Windows crossing midnight are supported. A start equal to end is invalid
    rather than ambiguously representing either zero or 24 hours.
    """
    weekdays: frozenset[int]
    start: time
    end: time
    timezone_name: str = "UTC"

    def __post_init__(self) -> None:
        if not self.weekdays or any(day not in range(7) for day in self.weekdays):
            raise ValueError("weekdays must contain ISO weekdays 0..6")
        if self.start == self.end:
            raise ValueError("maintenance window start and end must differ")
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(self.timezone_name)
        except Exception as exc:
            raise ValueError("unknown maintenance window timezone") from exc

    def contains(self, instant: datetime) -> bool:
        from zoneinfo import ZoneInfo
        if instant.tzinfo is None:
            raise ValueError("instant must be timezone-aware")
        local = instant.astimezone(ZoneInfo(self.timezone_name))
        weekday, current = local.weekday(), local.timetz().replace(tzinfo=None)
        if self.start < self.end:
            return weekday in self.weekdays and self.start <= current < self.end
        previous = (weekday - 1) % 7
        return ((weekday in self.weekdays and current >= self.start)
                or (previous in self.weekdays and current < self.end))


@dataclass(frozen=True)
class RolloutPolicy:
    strategy: DeploymentStrategy
    stages: tuple[int, ...]
    bake_minutes: int = 10
    max_error_rate: float = 0.02
    max_p95_latency_ms: float = 2000.0
    min_requests: int = 20
    auto_rollback: bool = True
    maintenance_window: MaintenanceWindow | None = None

    def __post_init__(self) -> None:
        if not self.stages or self.stages[-1] != 100:
            raise ValueError("rollout stages must finish at 100 percent")
        if any(value <= 0 or value > 100 for value in self.stages):
            raise ValueError("rollout stages must be between 1 and 100")
        if tuple(sorted(set(self.stages))) != self.stages:
            raise ValueError("rollout stages must be strictly increasing")
        if self.strategy == DeploymentStrategy.BLUE_GREEN and self.stages != (100,):
            raise ValueError("blue/green strategy must switch traffic in one 100% stage")
        if self.bake_minutes < 0 or self.min_requests < 1:
            raise ValueError("bake_minutes must be >= 0 and min_requests >= 1")
        if not 0 <= self.max_error_rate <= 1 or self.max_p95_latency_ms <= 0:
            raise ValueError("invalid health thresholds")


@dataclass(frozen=True)
class HealthSnapshot:
    requests: int
    errors: int
    p95_latency_ms: float

    @property
    def error_rate(self) -> float:
        return self.errors / self.requests if self.requests else 0.0


@dataclass
class Deployment:
    tenant_id: str
    service: str
    version: str
    previous_version: str
    policy: RolloutPolicy
    deployment_id: str = field(default_factory=lambda: str(uuid4()))
    state: DeploymentState = DeploymentState.PLANNED
    stage_index: int = -1
    traffic_percent: int = 0
    rollback_reason: str | None = None
    history: list[dict[str, object]] = field(default_factory=list)

    def __post_init__(self) -> None:
        for name in ("tenant_id", "service", "version", "previous_version"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        if self.version == self.previous_version:
            raise ValueError("new version must differ from previous version")

    @property
    def target_percent(self) -> int | None:
        if self.stage_index < 0 or self.stage_index >= len(self.policy.stages):
            return None
        return self.policy.stages[self.stage_index]

    def start(self, now: datetime | None = None) -> None:
        if self.state != DeploymentState.PLANNED:
            raise ValueError("only planned deployments can start")
        instant = now or datetime.now(timezone.utc)
        window = self.policy.maintenance_window
        if window is not None and not window.contains(instant):
            raise ValueError("outside configured maintenance window")
        self.state = DeploymentState.RUNNING
        self.stage_index = 0
        self.traffic_percent = self.target_percent or 0
        self._record("started", instant)

    def evaluate(self, health: HealthSnapshot, now: datetime | None = None) -> DeploymentState:
        if self.state != DeploymentState.RUNNING:
            raise ValueError("only running deployments can be evaluated")
        instant = now or datetime.now(timezone.utc)
        unhealthy = (
            health.requests < self.policy.min_requests
            or health.errors < 0 or health.errors > health.requests
            or health.p95_latency_ms < 0
            or health.error_rate > self.policy.max_error_rate
            or health.p95_latency_ms > self.policy.max_p95_latency_ms
        )
        if unhealthy:
            reason = ("insufficient_samples" if health.requests < self.policy.min_requests
                      else "health_threshold_exceeded")
            if self.policy.auto_rollback:
                self.rollback(reason, instant)
            else:
                self.state = DeploymentState.PAUSED
                self.rollback_reason = reason
                self._record("paused", instant, reason=reason)
            return self.state
        if self.stage_index == len(self.policy.stages) - 1:
            self.state = DeploymentState.SUCCEEDED
            self._record("succeeded", instant)
        else:
            self._record("stage_healthy", instant, traffic_percent=self.traffic_percent)
            self.stage_index += 1
            self.traffic_percent = self.target_percent or 0
        return self.state

    def rollback(self, reason: str, now: datetime | None = None) -> None:
        if self.state not in (DeploymentState.RUNNING, DeploymentState.PAUSED):
            raise ValueError("deployment cannot be rolled back from its current state")
        instant = now or datetime.now(timezone.utc)
        self.state = DeploymentState.ROLLING_BACK
        self.rollback_reason = reason.strip() or "operator_requested"
        self._record("rollback_started", instant, reason=self.rollback_reason)
        # Adapter must atomically restore the prior version/traffic target.
        self.traffic_percent = 0
        self.state = DeploymentState.ROLLED_BACK
        self._record("rolled_back", instant, restored_version=self.previous_version)

    def _record(self, event: str, instant: datetime, **details: object) -> None:
        self.history.append({"event": event, "at": instant.isoformat(), **details})


def build_rollout_policy(strategy: DeploymentStrategy, *, canary_steps: Iterable[int] = (5, 25, 50, 100),
                         gray_steps: Iterable[int] = (1, 5, 10, 25, 50, 100),
                         **kwargs: object) -> RolloutPolicy:
    """Construct safe defaults while allowing explicit policy overrides."""
    if strategy == DeploymentStrategy.BLUE_GREEN:
        stages = (100,)
    elif strategy == DeploymentStrategy.CANARY:
        stages = tuple(canary_steps)
    elif strategy == DeploymentStrategy.GRAY:
        stages = tuple(gray_steps)
    else:
        stages = (10, 25, 50, 75, 100)
    return RolloutPolicy(strategy=strategy, stages=stages, **kwargs)
