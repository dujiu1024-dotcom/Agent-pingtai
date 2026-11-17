"""Failure-isolated helpers for nested telemetry instrumentation."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from agent_platform.observability.trace_spans import current_trace_span


class NoopObservation:
    def update(self, **kwargs: Any) -> None:
        return None


@contextmanager
def safe_observation(
    observer: Any | None, *, name: str, input: dict[str, Any], metadata: dict[str, Any]
) -> Iterator[Any]:
    """Yield an observation span without allowing telemetry failure to break work."""
    active_span = current_trace_span()
    if active_span is not None:
        metadata.update({
            "trace_id": active_span.trace_id,
            "span_id": active_span.span_id,
            "parent_span_id": active_span.parent_span_id,
            "trace_component": active_span.component,
        })
    if observer is None:
        yield NoopObservation()
        return
    try:
        manager = observer.observe(name=name, input=input, metadata=metadata)
        span = manager.__enter__()
    except Exception:
        yield NoopObservation()
        return
    try:
        yield span
    except BaseException as exc:
        try:
            manager.__exit__(type(exc), exc, exc.__traceback__)
        except Exception:
            pass
        raise
    else:
        try:
            manager.__exit__(None, None, None)
        except Exception:
            pass


def safe_update(span: Any, **kwargs: Any) -> None:
    try:
        span.update(**kwargs)
    except Exception:
        pass


def safe_metric(metrics: Any | None, method: str, *args: Any, **kwargs: Any) -> None:
    if metrics is None:
        return
    try:
        getattr(metrics, method)(*args, **kwargs)
    except Exception:
        pass
