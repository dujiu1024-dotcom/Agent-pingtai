"""Deterministic alert evaluation with aggregation and cooldown suppression."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping, Protocol


@dataclass(frozen=True)
class AlertRule:
    rule_id: str
    metric_name: str
    threshold: float
    comparison: str = "gte"
    severity: str = "warning"
    cooldown: timedelta = timedelta(minutes=5)
    description: str = ""

    def __post_init__(self) -> None:
        if not self.rule_id.strip() or not self.metric_name.strip():
            raise ValueError("rule_id and metric_name are required")
        if self.comparison not in {"gte", "gt", "lte", "lt"}:
            raise ValueError("unsupported alert comparison")
        if self.severity not in {"info", "warning", "critical"}:
            raise ValueError("unsupported alert severity")
        if self.cooldown < timedelta(0):
            raise ValueError("cooldown must be non-negative")


@dataclass(frozen=True)
class AlertEvent:
    rule_id: str
    metric_name: str
    observed_value: float
    threshold: float
    severity: str
    fired_at: datetime
    description: str


class AlertNotifier(Protocol):
    def notify(self, event: AlertEvent) -> None: ...


class AlertManager:
    """Evaluate a metric snapshot; no background scheduler or implicit network calls."""

    def __init__(self, rules: tuple[AlertRule, ...], notifier: AlertNotifier, *,
                 clock: Callable[[], datetime] | None = None) -> None:
        ids = [rule.rule_id for rule in rules]
        if len(ids) != len(set(ids)):
            raise ValueError("alert rule ids must be unique")
        self.rules = rules
        self.notifier = notifier
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._last_fired: dict[str, datetime] = {}

    @staticmethod
    def _matches(value: float, rule: AlertRule) -> bool:
        return {
            "gte": value >= rule.threshold,
            "gt": value > rule.threshold,
            "lte": value <= rule.threshold,
            "lt": value < rule.threshold,
        }[rule.comparison]

    def evaluate(self, metrics: Mapping[str, float]) -> tuple[AlertEvent, ...]:
        now = self.clock()
        events: list[AlertEvent] = []
        for rule in self.rules:
            if rule.metric_name not in metrics:
                continue
            value = float(metrics[rule.metric_name])
            if not self._matches(value, rule):
                continue
            last = self._last_fired.get(rule.rule_id)
            if last is not None and now - last < rule.cooldown:
                continue
            event = AlertEvent(
                rule_id=rule.rule_id, metric_name=rule.metric_name,
                observed_value=value, threshold=rule.threshold,
                severity=rule.severity, fired_at=now,
                description=rule.description,
            )
            self.notifier.notify(event)
            self._last_fired[rule.rule_id] = now
            events.append(event)
        return tuple(events)
