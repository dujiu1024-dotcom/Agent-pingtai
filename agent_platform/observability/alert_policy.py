"""Alert inhibition, digest aggregation, retry scheduling and on-call rotation policies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from agent_platform.observability.alerts import AlertEvent

_SEVERITY = {"info": 0, "warning": 1, "critical": 2}


@dataclass(frozen=True)
class AlertInhibitionRule:
    """Suppress target rules while a sufficiently severe source rule is active."""

    source_rule_id: str
    target_rule_ids: frozenset[str]
    minimum_source_severity: str = "warning"

    def __post_init__(self) -> None:
        if not self.source_rule_id.strip() or not self.target_rule_ids:
            raise ValueError("inhibition source and target rules are required")
        if self.source_rule_id in self.target_rule_ids:
            raise ValueError("an inhibition rule cannot suppress itself")
        if self.minimum_source_severity not in _SEVERITY:
            raise ValueError("unsupported inhibition severity")


@dataclass(frozen=True)
class AlertDigest:
    """A bounded notification batch; source events remain individually durable."""

    metric_name: str
    severity: str
    events: tuple[AlertEvent, ...]
    first_fired_at: datetime
    last_fired_at: datetime


class AlertPolicyEngine:
    """Apply inhibition before digest grouping without dropping durable source events."""

    def __init__(
        self,
        *,
        inhibition_rules: Iterable[AlertInhibitionRule] = (),
        aggregation_window: timedelta = timedelta(seconds=30),
        max_digest_events: int = 50,
    ) -> None:
        if aggregation_window < timedelta(0) or aggregation_window > timedelta(hours=1):
            raise ValueError("aggregation window must be between zero and one hour")
        if isinstance(max_digest_events, bool) or not 1 <= max_digest_events <= 500:
            raise ValueError("max_digest_events must be between 1 and 500")
        self.inhibition_rules = tuple(inhibition_rules)
        self.aggregation_window = aggregation_window
        self.max_digest_events = max_digest_events

    def filter_inhibited(self, events: Iterable[AlertEvent]) -> tuple[AlertEvent, ...]:
        candidates = tuple(events)
        active = {event.rule_id: event for event in candidates}
        suppressed: set[str] = set()
        for rule in self.inhibition_rules:
            source = active.get(rule.source_rule_id)
            if source is None or _SEVERITY[source.severity] < _SEVERITY[rule.minimum_source_severity]:
                continue
            suppressed.update(rule.target_rule_ids.intersection(active))
        return tuple(event for event in candidates if event.rule_id not in suppressed)

    def aggregate(self, events: Iterable[AlertEvent]) -> tuple[AlertDigest, ...]:
        """Group near-simultaneous notifications; events themselves are not coalesced in storage."""
        ordered = sorted(events, key=lambda event: event.fired_at)
        groups: list[list[AlertEvent]] = []
        for event in ordered:
            match = next((
                group for group in groups
                if group[0].metric_name == event.metric_name
                and group[0].severity == event.severity
                and event.fired_at - group[0].fired_at <= self.aggregation_window
                and len(group) < self.max_digest_events
            ), None)
            if match is None:
                groups.append([event])
            else:
                match.append(event)
        return tuple(
            AlertDigest(
                metric_name=group[0].metric_name,
                severity=group[0].severity,
                events=tuple(group),
                first_fired_at=group[0].fired_at,
                last_fired_at=group[-1].fired_at,
            )
            for group in groups
        )

    def process(self, events: Iterable[AlertEvent]) -> tuple[tuple[AlertEvent, ...], tuple[AlertDigest, ...]]:
        filtered = self.filter_inhibited(events)
        return filtered, self.aggregate(filtered)


@dataclass(frozen=True)
class OnCallAssignment:
    user_id: str
    acknowledged_by_deadline: datetime


class OnCallRotation:
    """Deterministic UTC rotation with an explicit acknowledgement deadline."""

    def __init__(
        self,
        *,
        user_ids: tuple[str, ...],
        starts_at: datetime,
        rotation_interval: timedelta,
        acknowledgement_timeout: timedelta = timedelta(minutes=15),
    ) -> None:
        if not user_ids or any(not isinstance(user, str) or not user.strip() for user in user_ids):
            raise ValueError("at least one non-empty on-call user id is required")
        if len(set(user_ids)) != len(user_ids):
            raise ValueError("on-call user ids must be unique")
        if starts_at.tzinfo is None or starts_at.utcoffset() is None:
            raise ValueError("starts_at must be timezone-aware")
        if rotation_interval <= timedelta(0):
            raise ValueError("rotation_interval must be positive")
        if acknowledgement_timeout <= timedelta(0):
            raise ValueError("acknowledgement_timeout must be positive")
        self.user_ids = user_ids
        self.starts_at = starts_at.astimezone(timezone.utc)
        self.rotation_interval = rotation_interval
        self.acknowledgement_timeout = acknowledgement_timeout

    def assign(self, fired_at: datetime) -> OnCallAssignment:
        if fired_at.tzinfo is None or fired_at.utcoffset() is None:
            raise ValueError("fired_at must be timezone-aware")
        instant = fired_at.astimezone(timezone.utc)
        elapsed = max(timedelta(0), instant - self.starts_at)
        slot = int(elapsed // self.rotation_interval)
        return OnCallAssignment(
            user_id=self.user_ids[slot % len(self.user_ids)],
            acknowledged_by_deadline=instant + self.acknowledgement_timeout,
        )


@dataclass(frozen=True)
class AlertRetryPolicy:
    """Bounded exponential retry schedule for durable outbox attempts."""

    max_attempts: int = 8
    initial_delay: timedelta = timedelta(seconds=5)
    maximum_delay: timedelta = timedelta(hours=1)

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or not 1 <= self.max_attempts <= 100:
            raise ValueError("max_attempts must be between 1 and 100")
        if self.initial_delay <= timedelta(0) or self.maximum_delay < self.initial_delay:
            raise ValueError("retry delays must be positive and maximum >= initial")

    def delay_after(self, attempt: int) -> timedelta:
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise ValueError("attempt must be a positive integer")
        seconds = min(
            self.maximum_delay.total_seconds(),
            self.initial_delay.total_seconds() * (2 ** min(attempt - 1, 30)),
        )
        return timedelta(seconds=seconds)

    def exhausted(self, attempt: int) -> bool:
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 0:
            raise ValueError("attempt must be a non-negative integer")
        return attempt >= self.max_attempts
