"""Versioned, capability-aware Runtime Adapter contract shared by model and Agent runtimes."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping, Protocol

_CONTRACT_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?$")


def _version(value: str, *, label: str) -> tuple[int, int, int]:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a semantic version")
    match = _CONTRACT_VERSION.fullmatch(value)
    if match is None:
        raise ValueError(f"{label} must use MAJOR.MINOR.PATCH")
    return tuple(int(part) for part in match.groups())


class RuntimeAdapterKind(StrEnum):
    MODEL = "model"
    HARNESS = "harness"
    AGENT = "agent"


class RuntimeExecutionPhase(StrEnum):
    ACCEPTED = "accepted"
    PREPARING = "preparing"
    READY = "ready"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


_TERMINAL_PHASES = frozenset({
    RuntimeExecutionPhase.SUCCEEDED,
    RuntimeExecutionPhase.FAILED,
    RuntimeExecutionPhase.CANCELLED,
    RuntimeExecutionPhase.TIMED_OUT,
})


@dataclass(frozen=True)
class RuntimeCapabilities:
    """Features an adapter guarantees; absent features must be treated as unsupported."""

    modalities: frozenset[str] = frozenset({"text"})
    streaming: bool = False
    cancellation: bool = False
    resumable: bool = False
    structured_output: bool = False
    tool_calls: bool = False

    def __post_init__(self) -> None:
        supported = {"text", "image", "pdf", "audio", "video"}
        if not self.modalities or not self.modalities.issubset(supported):
            raise ValueError("adapter modalities must be a non-empty supported set")
        for name in ("streaming", "cancellation", "resumable", "structured_output", "tool_calls"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} capability must be boolean")


@dataclass(frozen=True)
class RuntimeAdapterManifest:
    adapter_id: str
    kind: RuntimeAdapterKind
    adapter_version: str
    min_contract_version: str
    max_contract_version: str
    capabilities: RuntimeCapabilities
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.adapter_id, str) or not self.adapter_id.strip():
            raise ValueError("adapter_id is required")
        if not isinstance(self.kind, RuntimeAdapterKind):
            raise ValueError("adapter kind must be a RuntimeAdapterKind")
        _version(self.adapter_version, label="adapter_version")
        minimum = _version(self.min_contract_version, label="min_contract_version")
        maximum = _version(self.max_contract_version, label="max_contract_version")
        if minimum > maximum or minimum[0] != maximum[0]:
            raise ValueError("contract compatibility range must be ordered within one major version")
        if not isinstance(self.capabilities, RuntimeCapabilities):
            raise ValueError("capabilities must be RuntimeCapabilities")
        if any(not isinstance(k, str) or not isinstance(v, str) for k, v in self.metadata.items()):
            raise ValueError("adapter metadata must contain string keys and values")

    def supports_contract(self, contract_version: str) -> bool:
        requested = _version(contract_version, label="contract_version")
        return (
            requested[0] == _version(self.min_contract_version, label="min_contract_version")[0]
            and _version(self.min_contract_version, label="min_contract_version")
            <= requested
            <= _version(self.max_contract_version, label="max_contract_version")
        )

    def require(self, required: RuntimeCapabilities) -> None:
        actual = self.capabilities
        missing_modalities = required.modalities - actual.modalities
        missing_flags = [
            name for name in ("streaming", "cancellation", "resumable", "structured_output", "tool_calls")
            if getattr(required, name) and not getattr(actual, name)
        ]
        if missing_modalities or missing_flags:
            details = sorted(missing_modalities) + sorted(missing_flags)
            raise UnsupportedRuntimeCapability(
                "adapter does not support required capabilities: " + ", ".join(details)
            )


class RuntimeAdapterCompatibilityError(RuntimeError):
    """Raised when an adapter cannot safely implement the host's contract version."""


class UnsupportedRuntimeCapability(RuntimeError):
    """Raised when a request requires a capability not declared by its adapter."""


def validate_adapter_compatibility(
    manifest: RuntimeAdapterManifest, *, host_contract_version: str,
) -> None:
    """Fail closed when host and adapter contract versions do not overlap."""
    if not manifest.supports_contract(host_contract_version):
        raise RuntimeAdapterCompatibilityError(
            f"adapter {manifest.adapter_id!r} does not support runtime contract "
            f"{host_contract_version}"
        )


@dataclass(frozen=True)
class RuntimeExecutionContext:
    tenant_id: str
    run_id: str
    actor_id: str
    request_id: str
    deadline_at: datetime | None = None
    correlation_id: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("tenant_id", "run_id", "actor_id", "request_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        if self.deadline_at is not None and self.deadline_at.tzinfo is None:
            raise ValueError("deadline_at must be timezone-aware")


@dataclass(frozen=True)
class RuntimeExecutionResult:
    output: Any
    phase: RuntimeExecutionPhase
    usage: Mapping[str, int] = field(default_factory=dict)
    provider_id: str | None = None
    provider_version: str | None = None
    model_id: str | None = None
    adapter_id: str | None = None
    adapter_version: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.phase not in _TERMINAL_PHASES:
            raise ValueError("execution result must have a terminal phase")
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in self.usage.values()
        ):
            raise ValueError("usage counters must be non-negative integers")


class RuntimeAdapter(Protocol):
    """Common execution port implemented by model, Harness and Agent adapters."""

    manifest: RuntimeAdapterManifest

    async def execute(
        self, *, context: RuntimeExecutionContext, request: Mapping[str, Any],
    ) -> RuntimeExecutionResult:
        """Execute one tenant/Run-bound request and return a normalized terminal result."""
        ...

    async def cancel(self, *, context: RuntimeExecutionContext) -> None:
        """Request cancellation for this exact tenant and Run; idempotent by contract."""
        ...


def validate_runtime_adapter(
    adapter: RuntimeAdapter, *, host_contract_version: str,
    required_capabilities: RuntimeCapabilities | None = None,
) -> RuntimeAdapterManifest:
    manifest = getattr(adapter, "manifest", None)
    if not isinstance(manifest, RuntimeAdapterManifest):
        raise TypeError("runtime adapter must expose a RuntimeAdapterManifest")
    if not callable(getattr(adapter, "execute", None)) or not callable(getattr(adapter, "cancel", None)):
        raise TypeError("runtime adapter must implement execute and cancel")
    validate_adapter_compatibility(manifest, host_contract_version=host_contract_version)
    if required_capabilities is not None:
        manifest.require(required_capabilities)
    return manifest
