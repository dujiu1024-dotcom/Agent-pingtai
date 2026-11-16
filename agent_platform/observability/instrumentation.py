"""Failure-isolated helpers for nested telemetry instrumentation."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator


class NoopObservation:
    def update(self, **kwargs: Any) -> None:
        return None


@contextmanager
def safe_observation(
    observer: Any | None, *, name: str, input: dict[str, Any], metadata: dict[str, Any]
) -> Iterator[Any]:
    """Yield an observation span without allowing telemetry failure to break work."""
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
