"""Atomic, validated runtime configuration reload with optimistic concurrency.

Configuration is published as immutable snapshots. Audit is written before
activation; listener failures are reported but never roll back an already
committed snapshot. Secret values are never copied into audit events.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any


class ConfigurationConflict(RuntimeError):
    """Raised when a reload was based on a stale configuration version."""


class ConfigurationValidationError(ValueError):
    """Raised when candidate configuration is invalid or unsafe."""


@dataclass(frozen=True)
class ConfigurationSnapshot:
    version: int
    digest: str
    activated_at: str
    values: Mapping[str, Any]


@dataclass(frozen=True)
class ConfigurationReloadResult:
    snapshot: ConfigurationSnapshot
    listener_errors: tuple[str, ...] = ()


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return copy.deepcopy(value)


def _canonical(candidate: Mapping[str, Any], max_bytes: int) -> tuple[dict[str, Any], str]:
    if not isinstance(candidate, Mapping) or any(not isinstance(key, str) or not key for key in candidate):
        raise ConfigurationValidationError("configuration must be a mapping with non-empty string keys")
    try:
        cloned = copy.deepcopy(dict(candidate))
        encoded = json.dumps(cloned, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError) as exc:
        raise ConfigurationValidationError("configuration must contain finite JSON-compatible values") from exc
    if len(encoded) > max_bytes:
        raise ConfigurationValidationError(f"configuration exceeds {max_bytes} bytes")
    return cloned, hashlib.sha256(encoded).hexdigest()


class HotConfigurationManager:
    """Manage process-local config snapshots; callers distribute reloads across workers."""

    def __init__(
        self,
        initial: Mapping[str, Any],
        *,
        validator: Callable[[Mapping[str, Any]], Any] | None = None,
        audit: Callable[[Mapping[str, Any]], Any] | None = None,
        max_bytes: int = 1024 * 1024,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        self._validator = validator
        self._audit = audit
        self._max_bytes = max_bytes
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[ConfigurationSnapshot, ConfigurationSnapshot], Any]] = []
        values, digest = _canonical(initial, max_bytes)
        self._validate(values)
        self._snapshot = ConfigurationSnapshot(
            version=1, digest=digest,
            activated_at=self._now().isoformat(), values=_freeze(values),
        )

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("configuration clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    def _validate(self, values: Mapping[str, Any]) -> None:
        if self._validator is None:
            return
        result = self._validator(values)
        if inspect.isawaitable(result):
            raise TypeError("configuration validator must be synchronous")

    @property
    def current(self) -> ConfigurationSnapshot:
        return self._snapshot

    def subscribe(self, listener: Callable[[ConfigurationSnapshot, ConfigurationSnapshot], Any]) -> None:
        if not callable(listener):
            raise TypeError("configuration listener must be callable")
        self._listeners.append(listener)

    async def reload(
        self, candidate: Mapping[str, Any], *, expected_version: int,
    ) -> ConfigurationReloadResult:
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1:
            raise ValueError("expected_version must be a positive integer")
        values, digest = _canonical(candidate, self._max_bytes)
        self._validate(values)
        async with self._lock:
            previous = self._snapshot
            if expected_version != previous.version:
                raise ConfigurationConflict(
                    f"stale configuration version {expected_version}; current is {previous.version}"
                )
            if digest == previous.digest:
                return ConfigurationReloadResult(previous)
            next_snapshot = ConfigurationSnapshot(
                version=previous.version + 1, digest=digest,
                activated_at=self._now().isoformat(), values=_freeze(values),
            )
            if self._audit is not None:
                event = {
                    "event": "runtime_configuration_reloaded",
                    "previous_version": previous.version,
                    "new_version": next_snapshot.version,
                    "previous_digest": previous.digest,
                    "new_digest": next_snapshot.digest,
                    "changed_keys": sorted(
                        key for key in set(previous.values) | set(next_snapshot.values)
                        if _thaw(previous.values.get(key)) != _thaw(next_snapshot.values.get(key))
                    ),
                    "activated_at": next_snapshot.activated_at,
                }
                result = self._audit(event)
                if inspect.isawaitable(result):
                    await result
            self._snapshot = next_snapshot
        listener_errors: list[str] = []
        for listener in tuple(self._listeners):
            try:
                result = listener(previous, next_snapshot)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                listener_errors.append(type(exc).__name__)
        return ConfigurationReloadResult(next_snapshot, tuple(listener_errors))

    def get(self, key: str, default: Any = None) -> Any:
        return self._snapshot.values.get(key, default)
