"""Ports implemented by open-source Harness and model-provider adapters."""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from agent_platform.contracts import ModelTemplateRef, RunContext

_SUPPORTED_MODALITIES = frozenset({"text", "image", "pdf", "audio", "video"})
_DEFAULT_MEDIA_MAX_BYTES = {
    "image": 20 * 1024 * 1024,
    "pdf": 50 * 1024 * 1024,
    "audio": 100 * 1024 * 1024,
    "video": 250 * 1024 * 1024,
}
_DEFAULT_MEDIA_MIME_TYPES = {
    "image": frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"}),
    "pdf": frozenset({"application/pdf"}),
    "audio": frozenset({
        "audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav",
        "audio/ogg", "audio/webm", "audio/mp4",
    }),
    "video": frozenset({"video/mp4", "video/webm", "video/quicktime"}),
}
RUNTIME_ADAPTER_CONTRACT_VERSION = "1.0.0"
_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def _parse_version(value: str, *, field_name: str) -> tuple[int, int, int]:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a semantic version")
    match = _SEMVER.fullmatch(value)
    if match is None:
        raise ValueError(f"{field_name} must use MAJOR.MINOR.PATCH semantic versioning")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _validate_media_reference(
    *, modality: str, resource_id: str, mime_type: str,
    size_bytes: int, sha256: str, filename: str | None,
) -> None:
    if not isinstance(modality, str) or modality not in _SUPPORTED_MODALITIES - {"text"}:
        raise ValueError("media modality must be image, pdf, audio or video")
    if not isinstance(resource_id, str) or not resource_id.strip():
        raise ValueError("media resource_id is required")
    if (
        any(ord(char) < 32 or ord(char) == 127 for char in resource_id)
        or any(char.isspace() for char in resource_id)
        or "://" in resource_id or "/" in resource_id or "\\" in resource_id
        or ".." in resource_id
    ):
        raise ValueError("media resource_id must be an opaque authorized resource identifier")
    if not isinstance(mime_type, str) or not mime_type.strip():
        raise ValueError("media mime_type is required")
    if any(ord(char) < 32 or ord(char) == 127 for char in mime_type):
        raise ValueError("media mime_type must not contain control characters")
    normalized = mime_type.lower().split(";", 1)[0].strip()
    valid_mime = {
        "image": re.fullmatch(r"image/[a-z0-9.+-]+", normalized) is not None,
        "pdf": normalized == "application/pdf",
        "audio": re.fullmatch(r"audio/[a-z0-9.+-]+", normalized) is not None,
        "video": re.fullmatch(r"video/[a-z0-9.+-]+", normalized) is not None,
    }[modality]
    if not valid_mime:
        raise ValueError("media mime_type does not match its declared modality")
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes <= 0:
        raise ValueError("media size_bytes must be a positive integer")
    if not isinstance(sha256, str) or re.fullmatch(r"[0-9a-fA-F]{64}", sha256) is None:
        raise ValueError("media sha256 must be a 64-character hexadecimal digest")
    if filename is not None:
        if (
            not isinstance(filename, str) or not filename.strip()
            or len(filename) > 255 or filename in {".", ".."}
            or "/" in filename or "\\" in filename
            or any(ord(char) < 32 or ord(char) == 127 for char in filename)
        ):
            raise ValueError("media filename must be a safe basename of at most 255 characters")


@dataclass(frozen=True)
class MediaInput:
    """Authorized media resource reference; never a user-supplied URL or filesystem path."""

    modality: str
    resource_id: str
    mime_type: str
    size_bytes: int
    sha256: str
    filename: str | None = None

    def __post_init__(self) -> None:
        _validate_media_reference(
            modality=self.modality, resource_id=self.resource_id,
            mime_type=self.mime_type, size_bytes=self.size_bytes,
            sha256=self.sha256, filename=self.filename,
        )


@dataclass(frozen=True)
class MediaOutput:
    """Media artifact reference produced by a trusted artifact store."""

    modality: str
    resource_id: str
    mime_type: str
    size_bytes: int
    sha256: str
    filename: str | None = None

    def __post_init__(self) -> None:
        _validate_media_reference(
            modality=self.modality, resource_id=self.resource_id,
            mime_type=self.mime_type, size_bytes=self.size_bytes,
            sha256=self.sha256, filename=self.filename,
        )


@dataclass(frozen=True)
class HarnessCapabilities:
    """Declared modality and execution features for one Harness adapter."""

    input_modalities: frozenset[str] = frozenset({"text"})
    output_modalities: frozenset[str] = frozenset({"text"})
    streaming: bool = False
    cancellation: bool = True
    structured_output: bool = False
    timeout: bool = False
    resume: bool = False
    tool_gateway: bool = False
    input_media_max_bytes: Mapping[str, int] = field(default_factory=dict)
    output_media_max_bytes: Mapping[str, int] = field(default_factory=dict)
    input_media_mime_types: Mapping[str, frozenset[str]] = field(default_factory=dict)
    output_media_mime_types: Mapping[str, frozenset[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.input_modalities.issubset(_SUPPORTED_MODALITIES):
            raise ValueError("Harness declares an unsupported input modality")
        if not self.output_modalities.issubset(_SUPPORTED_MODALITIES):
            raise ValueError("Harness declares an unsupported output modality")
        for direction, declared, limits, mime_types in (
            ("input", self.input_modalities, self.input_media_max_bytes, self.input_media_mime_types),
            ("output", self.output_modalities, self.output_media_max_bytes, self.output_media_mime_types),
        ):
            media_declared = declared - {"text"}
            if set(limits) - media_declared or set(mime_types) - media_declared:
                raise ValueError(f"Harness {direction} media policy references an undeclared modality")
            for modality, maximum in limits.items():
                if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum <= 0:
                    raise ValueError(f"Harness {direction} {modality} max bytes must be a positive integer")
            for modality, allowed in mime_types.items():
                if not allowed or any(
                    not isinstance(value, str)
                    or value.lower().split(";", 1)[0].strip() not in _DEFAULT_MEDIA_MIME_TYPES[modality]
                    for value in allowed
                ):
                    raise ValueError(f"Harness {direction} {modality} MIME allowlist is invalid")


@dataclass(frozen=True)
class RuntimeAdapterDescriptor:
    """Versioned, inspectable declaration for a runtime adapter implementation."""

    adapter_id: str
    adapter_version: str
    contract_version: str
    capabilities: HarnessCapabilities

    def __post_init__(self) -> None:
        if not isinstance(self.adapter_id, str) or not re.fullmatch(
            r"[a-z][a-z0-9_.-]{1,127}", self.adapter_id
        ):
            raise ValueError("adapter_id must be a stable lowercase identifier")
        _parse_version(self.adapter_version, field_name="adapter_version")
        _parse_version(self.contract_version, field_name="contract_version")
        if not isinstance(self.capabilities, HarnessCapabilities):
            raise TypeError("capabilities must be HarnessCapabilities")


class RuntimeAdapterCompatibilityError(RuntimeError):
    """Raised when an adapter cannot satisfy the platform Runtime contract."""


def ensure_runtime_adapter_compatible(
    descriptor: RuntimeAdapterDescriptor,
    *,
    required_contract_version: str = RUNTIME_ADAPTER_CONTRACT_VERSION,
) -> None:
    """Fail closed for unsupported contract majors or older contract revisions.

    A newer minor/patch revision within the same major is backward-compatible.
    Adapter package versions are informational and do not determine contract support.
    """
    required = _parse_version(required_contract_version, field_name="required_contract_version")
    provided = _parse_version(descriptor.contract_version, field_name="contract_version")
    if provided[0] != required[0] or provided < required:
        raise RuntimeAdapterCompatibilityError(
            f"adapter {descriptor.adapter_id} implements contract "
            f"{descriptor.contract_version}; platform requires {required_contract_version}"
        )


@dataclass(frozen=True)
class RuntimeAdapterDescriptor:
    """Versioned, inspectable declaration for a runtime adapter implementation."""

    adapter_id: str
    adapter_version: str
    contract_version: str
    capabilities: HarnessCapabilities

    def __post_init__(self) -> None:
        if not isinstance(self.adapter_id, str) or not re.fullmatch(
            r"[a-z][a-z0-9_.-]{1,127}", self.adapter_id
        ):
            raise ValueError("adapter_id must be a stable lowercase identifier")
        _parse_version(self.adapter_version, field_name="adapter_version")
        _parse_version(self.contract_version, field_name="contract_version")
        if not isinstance(self.capabilities, HarnessCapabilities):
            raise TypeError("capabilities must be HarnessCapabilities")


class RuntimeAdapterCompatibilityError(RuntimeError):
    """Raised when an adapter cannot satisfy the platform Runtime contract."""


def ensure_runtime_adapter_compatible(
    descriptor: RuntimeAdapterDescriptor,
    *,
    required_contract_version: str = RUNTIME_ADAPTER_CONTRACT_VERSION,
) -> None:
    """Fail closed for unsupported contract majors or older contract revisions.

    A newer minor/patch revision within the same major is backward-compatible.
    Adapter package versions are informational and do not determine contract support.
    """
    required = _parse_version(required_contract_version, field_name="required_contract_version")
    provided = _parse_version(descriptor.contract_version, field_name="contract_version")
    if provided[0] != required[0] or provided < required:
        raise RuntimeAdapterCompatibilityError(
            f"adapter {descriptor.adapter_id} implements contract "
            f"{descriptor.contract_version}; platform requires {required_contract_version}"
        )


class UnsupportedHarnessCapability(RuntimeError):
    """Raised when a request requires a feature not declared by its adapter."""


class CapabilityAwareHarnessAdapter:
    """Fail-closed wrapper enforcing declared Harness capabilities."""

    def __init__(self, *, adapter: Any, capabilities: HarnessCapabilities) -> None:
        if not callable(getattr(adapter, "run", None)):
            raise TypeError("adapter must provide an async run(request) method")
        self.adapter = adapter
        self.capabilities = capabilities

    def _validate_request(self, request: "HarnessRequest") -> None:
        requested_inputs = {"text"} if request.input_text else set()
        requested_inputs.update(item.modality for item in request.media_inputs)
        unsupported = requested_inputs - self.capabilities.input_modalities
        if unsupported:
            raise UnsupportedHarnessCapability(
                "Harness does not declare input modalities: " + ", ".join(sorted(unsupported))
            )
        self._validate_media_items(
            request.media_inputs, direction="input",
            max_bytes=self.capabilities.input_media_max_bytes,
            mime_types=self.capabilities.input_media_mime_types,
        )
        if request.output_schema is not None and not self.capabilities.structured_output:
            raise UnsupportedHarnessCapability("Harness does not declare structured output support")
        if request.allowed_tools and not self.capabilities.tool_gateway:
            raise UnsupportedHarnessCapability("Harness does not declare Tool Gateway integration")
        if request.timeout_seconds is not None and not self.capabilities.timeout:
            raise UnsupportedHarnessCapability("Harness does not declare timeout enforcement")
        if request.resume and not self.capabilities.resume:
            raise UnsupportedHarnessCapability("Harness does not declare checkpoint resume support")

    def _validate_result(self, result: "HarnessResult") -> None:
        produced_outputs = {"text"} if result.output_text else set()
        produced_outputs.update(item.modality for item in result.media_outputs)
        unsupported = produced_outputs - self.capabilities.output_modalities
        if unsupported:
            raise UnsupportedHarnessCapability(
                "Harness does not declare output modalities: "
                + ", ".join(sorted(unsupported))
            )
        self._validate_media_items(
            result.media_outputs, direction="output",
            max_bytes=self.capabilities.output_media_max_bytes,
            mime_types=self.capabilities.output_media_mime_types,
        )

    def _validate_media_items(self, items, *, direction, max_bytes, mime_types) -> None:
        for item in items:
            maximum = max_bytes.get(item.modality, _DEFAULT_MEDIA_MAX_BYTES[item.modality])
            if item.size_bytes > maximum:
                raise UnsupportedHarnessCapability(
                    f"Harness {direction} {item.modality} exceeds max size of {maximum} bytes"
                )
            allowed = mime_types.get(item.modality, _DEFAULT_MEDIA_MIME_TYPES[item.modality])
            normalized = item.mime_type.lower().split(";", 1)[0].strip()
            if normalized not in allowed:
                raise UnsupportedHarnessCapability(
                    f"Harness {direction} {item.modality} MIME type {normalized} is not supported"
                )

    async def run(self, request: "HarnessRequest") -> "HarnessResult":
        self._validate_request(request)
        result = await self.adapter.run(request)
        self._validate_result(result)
        return result

    async def stream(self, request: "HarnessRequest") -> AsyncIterator["HarnessStreamEvent"]:
        self._validate_request(request)
        if not self.capabilities.streaming:
            raise UnsupportedHarnessCapability("Harness does not declare streaming support")
        stream = getattr(self.adapter, "stream", None)
        if not callable(stream):
            raise UnsupportedHarnessCapability("Harness declares streaming but has no stream method")
        async for event in stream(request):
            if event.event_type == "completed" and event.result is not None:
                self._validate_result(event.result)
            yield event

    async def cancel(self, *, run_id: str) -> None:
        if not self.capabilities.cancellation:
            raise UnsupportedHarnessCapability("Harness does not declare cancellation support")
        cancel = getattr(self.adapter, "cancel", None)
        if not callable(cancel):
            raise UnsupportedHarnessCapability("Harness declares cancellation but has no cancel method")
        await cancel(run_id=run_id)


@dataclass(frozen=True)
class ToolCall:
    tool_name: str
    arguments: dict[str, Any]
    call_id: str


@dataclass(frozen=True)
class HarnessRequest:
    context: RunContext
    instructions: str
    input_text: str
    allowed_tools: Sequence[str] = ()
    output_schema: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    media_inputs: Sequence[MediaInput] = ()
    timeout_seconds: float | None = None
    resume: bool = False
    checkpoint_ref: str | None = None

    def __post_init__(self) -> None:
        if self.timeout_seconds is not None and (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a finite positive number")
        if self.resume and not self.checkpoint_ref:
            raise ValueError("resume requires a durable checkpoint_ref")
        if self.checkpoint_ref is not None and not self.resume:
            raise ValueError("checkpoint_ref requires resume=True")


@dataclass(frozen=True)
class HarnessResult:
    output_text: str
    tool_calls: tuple[ToolCall, ...] = ()
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str = "completed"
    trace_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    media_outputs: tuple[MediaOutput, ...] = ()


@dataclass(frozen=True)
class HarnessStreamEvent:
    event_type: str
    text_delta: str | None = None
    tool_name: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    result: HarnessResult | None = None


class HarnessAdapter(Protocol):
    """Adapter around an existing agent execution engine; no custom loop here."""

    async def run(self, request: HarnessRequest) -> HarnessResult:
        """Execute one supervised agent turn and return a normalized result."""
        ...


class HarnessStreamingAdapter(Protocol):
    def stream(self, request: HarnessRequest) -> AsyncIterator[HarnessStreamEvent]:
        """Stream normalized text, Gateway tool and completion events."""
        ...


class HarnessExecutionLifecycle(Protocol):
    """Durable lifecycle observer called around each adapter execution."""

    async def started(
        self, *, request: HarnessRequest, adapter: RuntimeAdapterDescriptor,
    ) -> None: ...

    async def succeeded(
        self, *, request: HarnessRequest, adapter: RuntimeAdapterDescriptor,
        result: HarnessResult,
    ) -> None: ...

    async def failed(
        self, *, request: HarnessRequest, adapter: RuntimeAdapterDescriptor,
        error: Exception,
    ) -> None: ...

    async def timed_out(
        self, *, request: HarnessRequest, adapter: RuntimeAdapterDescriptor,
        error: TimeoutError,
    ) -> None: ...

    async def cancelled(
        self, *, request: HarnessRequest, adapter: RuntimeAdapterDescriptor,
    ) -> None: ...


class LifecycleManagedHarnessAdapter:
    """Apply compatibility, capability and lifecycle contracts to a Harness adapter."""

    def __init__(
        self,
        *,
        adapter: Any,
        descriptor: RuntimeAdapterDescriptor,
        lifecycle: HarnessExecutionLifecycle,
        required_contract_version: str = RUNTIME_ADAPTER_CONTRACT_VERSION,
    ) -> None:
        ensure_runtime_adapter_compatible(
            descriptor, required_contract_version=required_contract_version,
        )
        self.descriptor = descriptor
        self.lifecycle = lifecycle
        self.adapter = CapabilityAwareHarnessAdapter(
            adapter=adapter, capabilities=descriptor.capabilities,
        )

    async def run(self, request: "HarnessRequest") -> "HarnessResult":
        await self.lifecycle.started(request=request, adapter=self.descriptor)
        try:
            if request.timeout_seconds is None:
                result = await self.adapter.run(request)
            else:
                async with asyncio.timeout(request.timeout_seconds):
                    result = await self.adapter.run(request)
        except asyncio.CancelledError:
            await self.lifecycle.cancelled(request=request, adapter=self.descriptor)
            raise
        except TimeoutError as exc:
            await self.lifecycle.timed_out(
                request=request, adapter=self.descriptor, error=exc,
            )
            raise
        except Exception as exc:
            await self.lifecycle.failed(
                request=request, adapter=self.descriptor, error=exc,
            )
            raise
        await self.lifecycle.succeeded(
            request=request, adapter=self.descriptor, result=result,
        )
        return result

    async def stream(self, request: "HarnessRequest") -> AsyncIterator["HarnessStreamEvent"]:
        await self.lifecycle.started(request=request, adapter=self.descriptor)
        completed = False
        async def consume_stream() -> AsyncIterator[HarnessStreamEvent]:
            nonlocal completed
            async for event in self.adapter.stream(request):
                if event.event_type == "completed":
                    if event.result is None:
                        raise RuntimeError("Harness completion event must include a normalized result")
                    if completed:
                        raise RuntimeError("Harness stream emitted more than one completion event")
                    completed = True
                    await self.lifecycle.succeeded(
                        request=request, adapter=self.descriptor, result=event.result,
                    )
                yield event
            if not completed:
                raise RuntimeError("Harness stream ended without a completion event")
        try:
            if request.timeout_seconds is None:
                async for _event in consume_stream():
                    yield _event
            else:
                async with asyncio.timeout(request.timeout_seconds):
                    async for _event in consume_stream():
                        yield _event
        except asyncio.CancelledError:
            await self.lifecycle.cancelled(request=request, adapter=self.descriptor)
            raise
        except TimeoutError as exc:
            await self.lifecycle.timed_out(
                request=request, adapter=self.descriptor, error=exc,
            )
            raise
        except Exception as exc:
            await self.lifecycle.failed(
                request=request, adapter=self.descriptor, error=exc,
            )
            raise

    async def cancel(self, *, run_id: str) -> None:
        await self.adapter.cancel(run_id=run_id)


class ModelProvider(Protocol):
    async def complete(
        self,
        *,
        template: ModelTemplateRef,
        messages: Sequence[dict[str, Any]],
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve model and provider settings through a versioned template."""
        ...
