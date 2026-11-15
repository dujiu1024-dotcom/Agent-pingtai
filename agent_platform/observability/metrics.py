"""Low-cardinality metrics port and in-memory Prometheus exposition."""

from __future__ import annotations

import re
import threading
from collections import defaultdict
from collections.abc import Mapping

_METRIC_NAME = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*\Z")
_ALLOWED_LABELS = frozenset({"component", "status", "model_template", "finish_reason", "operation"})


def _normalize(name: str, labels: Mapping[str, str] | None) -> tuple[str, tuple[tuple[str, str], ...]]:
    if not _METRIC_NAME.fullmatch(name):
        raise ValueError("invalid metric name")
    normalized = tuple(sorted((str(key), str(value)[:100]) for key, value in (labels or {}).items()))
    if any(key not in _ALLOWED_LABELS for key, _ in normalized):
        raise ValueError("high-cardinality or unsupported metric label")
    return name, normalized


class InMemoryMetricsRecorder:
    """Thread-safe recorder intended for tests and single-process deployments.

    Production deployments should export the same metric contract to a
    Prometheus/OpenTelemetry backend rather than relying on process memory.
    """

    def __init__(self) -> None:
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._observations: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def increment(self, name: str, value: float = 1.0, *, labels: Mapping[str, str] | None = None) -> None:
        if value < 0:
            raise ValueError("counter increments must be non-negative")
        key = _normalize(name, labels)
        with self._lock:
            self._counters[key] += value

    def observe(self, name: str, value: float, *, labels: Mapping[str, str] | None = None) -> None:
        if value < 0:
            raise ValueError("observations must be non-negative")
        key = _normalize(name, labels)
        with self._lock:
            self._observations[key].append(float(value))

    def snapshot(self) -> dict[str, dict[str, float | int]]:
        with self._lock:
            counters = {
                self._format_key(key): value for key, value in self._counters.items()
            }
            observations = {
                self._format_key(key): len(values)
                for key, values in self._observations.items()
            }
            return {"counters": counters, "observation_counts": observations}

    @staticmethod
    def _format_key(key: tuple[str, tuple[tuple[str, str], ...]]) -> str:
        name, labels = key
        suffix = ",".join(f'{k}="{v}"' for k, v in labels)
        return f"{name}{{{suffix}}}" if suffix else name

    def render_prometheus(self) -> str:
        with self._lock:
            lines: list[str] = []
            for key, value in sorted(self._counters.items(), key=lambda item: item[0][0]):
                lines.append(f"{self._format_key(key)} {value}")
            for key, values in sorted(self._observations.items(), key=lambda item: item[0][0]):
                name, labels = key
                count_key = self._format_key((name + "_count", labels))
                sum_key = self._format_key((name + "_sum", labels))
                lines.append(f"{count_key} {len(values)}")
                lines.append(f"{sum_key} {sum(values)}")
            return "\n".join(lines) + ("\n" if lines else "")
