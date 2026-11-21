"""Host-injected operational capacity and certificate-expiry monitoring.

This module performs no implicit network or cloud calls. Production hosts supply
bounded probes for infrastructure, database, queue, object storage and certificates.
"""
from __future__ import annotations

import inspect
import math
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any


class OperationalHealthMonitor:
    """Evaluate capacity ratios and certificate expiry using trusted host probes."""

    def __init__(
        self,
        *,
        capacity_probes: Mapping[str, Callable[[], Any]],
        certificate_expiry_probes: Mapping[str, Callable[[], Any]],
        warning_ratio: float = 0.80,
        critical_ratio: float = 0.95,
        certificate_warning_days: int = 30,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not 0 < warning_ratio < critical_ratio <= 1:
            raise ValueError("capacity ratios must satisfy 0 < warning < critical <= 1")
        if isinstance(certificate_warning_days, bool) or not isinstance(certificate_warning_days, int) or certificate_warning_days < 1:
            raise ValueError("certificate_warning_days must be a positive integer")
        self._capacity_probes = self._validate_probes(capacity_probes, "capacity")
        self._certificate_probes = self._validate_probes(certificate_expiry_probes, "certificate")
        self.warning_ratio = float(warning_ratio)
        self.critical_ratio = float(critical_ratio)
        self.certificate_warning_days = certificate_warning_days
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _validate_probes(probes: Mapping[str, Callable[[], Any]], label: str) -> dict[str, Callable[[], Any]]:
        if not isinstance(probes, Mapping):
            raise TypeError(f"{label}_probes must be a mapping")
        result = dict(probes)
        if any(
            not isinstance(name, str) or not name.strip() or not callable(probe)
            for name, probe in result.items()
        ):
            raise ValueError(f"{label} probes require non-empty names and callables")
        return result

    @staticmethod
    async def _invoke(probe: Callable[[], Any]) -> Any:
        value = probe()
        return await value if inspect.isawaitable(value) else value

    @staticmethod
    def _capacity_value(value: Any) -> tuple[float, float]:
        if not isinstance(value, Mapping) or set(value) != {"used", "capacity"}:
            raise ValueError("capacity probe must return exactly used and capacity")
        used, capacity = value["used"], value["capacity"]
        if (
            isinstance(used, bool) or not isinstance(used, (int, float))
            or isinstance(capacity, bool) or not isinstance(capacity, (int, float))
            or not math.isfinite(used) or not math.isfinite(capacity)
            or used < 0 or capacity <= 0
        ):
            raise ValueError("capacity probe returned invalid bounds")
        return float(used), float(capacity)

    async def evaluate(self) -> dict[str, Any]:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("monitor clock must return a timezone-aware datetime")
        now = now.astimezone(timezone.utc)
        capacity: dict[str, Any] = {}
        certificates: dict[str, Any] = {}
        statuses: list[str] = []

        for name, probe in self._capacity_probes.items():
            try:
                used, limit = self._capacity_value(await self._invoke(probe))
                ratio = used / limit
                status = (
                    "critical" if ratio >= self.critical_ratio
                    else "warning" if ratio >= self.warning_ratio
                    else "ok"
                )
                capacity[name] = {
                    "status": status, "used": used, "capacity": limit,
                    "utilization_ratio": round(ratio, 6),
                }
            except Exception:
                status = "unavailable"
                capacity[name] = {"status": status}
            statuses.append(status)

        for name, probe in self._certificate_probes.items():
            try:
                expires_at = await self._invoke(probe)
                if not isinstance(expires_at, datetime) or expires_at.tzinfo is None or expires_at.utcoffset() is None:
                    raise ValueError("certificate expiry must be timezone-aware")
                seconds = (expires_at.astimezone(timezone.utc) - now).total_seconds()
                days = seconds / 86400
                status = "critical" if seconds <= 0 else "warning" if days <= self.certificate_warning_days else "ok"
                certificates[name] = {
                    "status": status,
                    "expires_at_utc": expires_at.astimezone(timezone.utc).isoformat(),
                    "days_remaining": round(days, 3),
                }
            except Exception:
                status = "unavailable"
                certificates[name] = {"status": status}
            statuses.append(status)

        overall = (
            "unavailable" if "unavailable" in statuses
            else "critical" if "critical" in statuses
            else "warning" if "warning" in statuses
            else "ok"
        )
        return {
            "status": overall,
            "observed_at_utc": now.isoformat(),
            "capacity": capacity,
            "certificates": certificates,
        }
