"""Production Prometheus client metrics adapter with bounded label cardinality."""
from __future__ import annotations

import math
import re
import threading
from collections.abc import Mapping
from typing import Any

_METRIC_NAME = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*\Z")
_ALLOWED_LABELS = frozenset({
    "component", "status", "model_template", "finish_reason", "operation", "queue",
})


class PrometheusClientMetricsRecorder:
    """Export the platform metrics port through prometheus-client.

    A private CollectorRegistry avoids process-global collector collisions.
    Metric names have a single type and a fixed label-name set for the lifetime
    of this recorder. Only the platform's bounded label vocabulary is allowed;
    run, tenant, user, prompt and request identifiers must never become labels.
    """

    def __init__(self, *, registry: Any | None = None) -> None:
        try:
            from prometheus_client import CollectorRegistry
        except ImportError as exc:
            raise RuntimeError(
                "Install prometheus-client>=0.21,<1 to enable production metrics"
            ) from exc
        self._registry = registry if registry is not None else CollectorRegistry(auto_describe=True)
        self._metrics: dict[str, tuple[str, tuple[str, ...], Any]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _validate(
        name: str, value: float, labels: Mapping[str, str] | None
    ) -> tuple[float, dict[str, str], tuple[str, ...]]:
        if not isinstance(name, str) or not _METRIC_NAME.fullmatch(name):
            raise ValueError("invalid metric name")
        if name.endswith(("_bucket", "_created")) or name.endswith("_sum") or name.endswith("_count"):
            raise ValueError("metric name uses a reserved Prometheus suffix")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("metric value must be a finite number")
        normalized: dict[str, str] = {}
        for key, raw in (labels or {}).items():
            if key not in _ALLOWED_LABELS:
                raise ValueError("high-cardinality or unsupported metric label")
            if not isinstance(raw, str) or not raw or len(raw) > 100:
                raise ValueError("metric label values must be non-empty strings of at most 100 characters")
            normalized[key] = raw
        return float(value), normalized, tuple(sorted(normalized))

    def _metric(self, name: str, kind: str, label_names: tuple[str, ...]) -> Any:
        existing = self._metrics.get(name)
        if existing is not None:
            existing_kind, existing_labels, metric = existing
            if existing_kind != kind or existing_labels != label_names:
                raise ValueError("metric type and label names must remain stable")
            return metric
        from prometheus_client import Counter, Histogram

        if kind == "counter":
            metric = Counter(
                name, f"Platform counter: {name}", labelnames=label_names,
                registry=self._registry,
            )
        elif kind == "gauge":
            from prometheus_client import Gauge
            metric = Gauge(
                name, f"Platform gauge: {name}", labelnames=label_names,
                registry=self._registry,
            )
        else:
            metric = Histogram(
                name, f"Platform observation: {name}", labelnames=label_names,
                registry=self._registry,
            )
        self._metrics[name] = (kind, label_names, metric)
        return metric

    @staticmethod
    def _child(metric: Any, labels: Mapping[str, str]) -> Any:
        return metric.labels(**dict(labels)) if labels else metric

    def increment(
        self, name: str, value: float = 1.0, *, labels: Mapping[str, str] | None = None
    ) -> None:
        amount, normalized, label_names = self._validate(name, value, labels)
        if amount < 0:
            raise ValueError("counter increments must be non-negative")
        with self._lock:
            metric = self._metric(name, "counter", label_names)
            self._child(metric, normalized).inc(amount)

    def observe(
        self, name: str, value: float, *, labels: Mapping[str, str] | None = None
    ) -> None:
        amount, normalized, label_names = self._validate(name, value, labels)
        if amount < 0:
            raise ValueError("observations must be non-negative")
        with self._lock:
            metric = self._metric(name, "histogram", label_names)
            self._child(metric, normalized).observe(amount)

    def set_gauge(
        self, name: str, value: float, *, labels: Mapping[str, str] | None = None
    ) -> None:
        amount, normalized, label_names = self._validate(name, value, labels)
        with self._lock:
            metric = self._metric(name, "gauge", label_names)
            self._child(metric, normalized).set(amount)

    def render_prometheus(self) -> str:
        from prometheus_client import generate_latest

        with self._lock:
            return generate_latest(self._registry).decode("utf-8")

    @property
    def registry(self) -> Any:
        """Expose the private registry for controlled integration with a WSGI/ASGI server."""
        return self._registry

    def export_json(self) -> list[dict[str, Any]]:
        """Export all registered metrics as a JSON-friendly list."""
        export: list[dict[str, Any]] = []
        for family in self._registry.collect():
            samples: list[dict[str, Any]] = []
            for sample in family.samples:
                labels = dict(sample.labels) if sample.labels else {}
                samples.append({
                    "name": sample.name,
                    "value": sample.value,
                    "labels": labels,
                })
            export.append({
                "name": family.name,
                "type": family.type,
                "help": family.help,
                "samples": samples,
            })
        return export
