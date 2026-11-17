"""Dependency-aware liveness and readiness probes for service health endpoints.

Checks are provider-neutral. Database drivers and service-specific clients are
injected by the application; this module never owns credentials or connections.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from time import monotonic
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ProbeStatus(str, Enum):
    UP = "up"
    DOWN = "down"
    DEGRADED = "degraded"


@dataclass(frozen=True)
class ProbeResult:
    name: str
    status: ProbeStatus
    critical: bool
    checked_at: datetime
    duration_ms: float
    detail: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("probe name is required")
        if self.checked_at.tzinfo is None:
            raise ValueError("checked_at must be timezone-aware")
        if self.duration_ms < 0:
            raise ValueError("duration_ms cannot be negative")


@dataclass(frozen=True)
class HealthReport:
    status: ProbeStatus
    ready: bool
    checked_at: datetime
    probes: tuple[ProbeResult, ...]
    failed_critical: tuple[str, ...] = ()

    @property
    def live(self) -> bool:
        # Liveness means the process can answer; dependency outages should not
        # trigger restart loops. Readiness carries dependency availability.
        return True

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "ready": self.ready,
            "live": self.live,
            "checked_at": self.checked_at.isoformat(),
            "failed_critical": list(self.failed_critical),
            "probes": [
                {
                    "name": item.name,
                    "status": item.status.value,
                    "critical": item.critical,
                    "checked_at": item.checked_at.isoformat(),
                    "duration_ms": round(item.duration_ms, 3),
                    **({"detail": item.detail} if item.detail else {}),
                }
                for item in self.probes
            ],
        }


Probe = Callable[[], None]


@dataclass(frozen=True)
class ProbeSpec:
    name: str
    check: Probe
    critical: bool = True

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("probe name is required")
        if not callable(self.check):
            raise ValueError("probe check must be callable")


@dataclass
class HealthProbeRegistry:
    """Runs named dependency checks and aggregates readiness deterministically."""

    probes: list[ProbeSpec] = field(default_factory=list)

    def register(self, name: str, check: Probe, *, critical: bool = True) -> None:
        if any(item.name == name for item in self.probes):
            raise ValueError(f"duplicate health probe: {name}")
        self.probes.append(ProbeSpec(name=name, check=check, critical=critical))

    def run(self, now: datetime | None = None) -> HealthReport:
        checked_at = now or datetime.now(timezone.utc)
        if checked_at.tzinfo is None:
            raise ValueError("health check time must be timezone-aware")
        results: list[ProbeResult] = []
        failed_critical: list[str] = []
        for spec in self.probes:
            started = monotonic()
            detail = None
            try:
                spec.check()
                status = ProbeStatus.UP
            except Exception as exc:  # isolate a dependency failure from other probes
                status = ProbeStatus.DOWN
                # Do not leak exception text, URLs, credentials, SQL, or response bodies.
                detail = type(exc).__name__
                if spec.critical:
                    failed_critical.append(spec.name)
            results.append(ProbeResult(
                name=spec.name, status=status, critical=spec.critical,
                checked_at=checked_at, duration_ms=(monotonic() - started) * 1000,
                detail=detail,
            ))
        ready = not failed_critical
        status = (ProbeStatus.DOWN if not ready else
                  ProbeStatus.DEGRADED if any(x.status == ProbeStatus.DOWN for x in results)
                  else ProbeStatus.UP)
        return HealthReport(status, ready, checked_at, tuple(results),
                            tuple(failed_critical))


def database_connection_probe(connect: Callable[[], object]) -> Probe:
    """Create a short-lived DB connection and execute a minimal SELECT 1.

    The returned connection must expose execute('SELECT 1') and close().
    Drivers with a different API should wrap it in this tiny adapter.
    """
    if not callable(connect):
        raise ValueError("connect must be callable")

    def check() -> None:
        connection = connect()
        try:
            execute = getattr(connection, "execute", None)
            if not callable(execute):
                raise TypeError("database connection must expose execute")
            result = execute("SELECT 1")
            fetchone = getattr(result, "fetchone", None)
            if callable(fetchone):
                row = fetchone()
                if row is None or row[0] != 1:
                    raise RuntimeError("database validation query returned unexpected result")
        finally:
            close = getattr(connection, "close", None)
            if callable(close):
                close()
    return check


def http_dependency_probe(
    url: str,
    *,
    timeout_seconds: float = 2.0,
    expected_statuses: Iterable[int] = (200,),
    headers: dict[str, str] | None = None,
) -> Probe:
    """Build an HTTP(S) reachability check for a configured external API.

    Configure a lightweight health endpoint, not a costly business API route.
    """
    from urllib.parse import urlsplit

    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("dependency URL must be an absolute HTTP(S) URL")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    accepted = frozenset(expected_statuses)
    if not accepted or any(code < 100 or code > 599 for code in accepted):
        raise ValueError("expected_statuses must contain valid HTTP status codes")

    def check() -> None:
        request = Request(url, headers=headers or {}, method="GET")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                status = response.status
        except HTTPError as exc:
            status = exc.code
        except (URLError, TimeoutError, OSError) as exc:
            raise ConnectionError("external dependency unavailable") from exc
        if status not in accepted:
            raise RuntimeError(f"external dependency returned HTTP {status}")
    return check


def build_health_registry(
    *,
    database_connect: Callable[[], object] | None = None,
    external_apis: dict[str, str] | None = None,
    extra_checks: dict[str, Probe] | None = None,
    timeout_seconds: float = 2.0,
) -> HealthProbeRegistry:
    """Convenience wiring for DB, external HTTP APIs and application dependencies."""
    registry = HealthProbeRegistry()
    if database_connect is not None:
        registry.register("database", database_connection_probe(database_connect))
    for name, url in (external_apis or {}).items():
        registry.register(name, http_dependency_probe(url, timeout_seconds=timeout_seconds))
    for name, check in (extra_checks or {}).items():
        registry.register(name, check)
    return registry
