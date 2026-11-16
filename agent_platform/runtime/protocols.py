""""Ports implemented by open-source Harness and model-provider adapters."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from agent_platform.contracts import ModelTemplateRef, RunContext

_SUPPORTED_MODALITIES = frozenset({"text", "image", "pdf", "audio", "video"})
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
    if modality not in _SUPPORTED_MODALITIES - {"text"}:
        raise ValueError("media modality must be image, pdf, audio or video")
    if not isinstance(resource_id, str) or not resource_id.strip():
        raise ValueError("media resource_id is required")
    if (
        "://" in resource_id or "/" in resource_id or "\\" in resource_id
        or ".." in resource_id
    ):
        raise ValueError("media resource_id must be an opaque authorized resource identifier")
    if not isinstance(mime_type, str) or not mime_type.strip():
        raise ValueError("media mime_type is required")
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
            or len(filename) > 255 or "/" in filename or "\\" in filename
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
    timeout: bool = True
    resume: bool = False
    tool_gateway: bool = False

    def __post_init__(self) -> None:
        if not self.input_modalities.issubset(_SUPPORTED_MODALITIES):
            raise ValueError("Harness declares an unsupported input modality")
        if not self.output_modalities.issubset(_SUPPORTED_MODALITIES):
            raise ValueError("Harness declares an unsupported output modality")


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
        if request.output_schema is not None and not self.capabilities.structured_output:
            raise UnsupportedHarnessCapability("Harness does not declare structured output support")

    def _validate_result(self, result: "HarnessResult") -> None:
        produced_outputs = {"text"} if result.output_text else set()
        produced_outputs.update(item.modality for item in result.media_outputs)
        unsupported = produced_outputs - self.capabilities.output_modalities
        if unsupported:
            raise UnsupportedHarnessCapability(
                "Harness does not declare output modalities: "
                + ", ".join(sorted(unsupported))
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
            result = await self.adapter.run(request)
        except asyncio.CancelledError:
            await self.lifecycle.cancelled(request=request, adapter=self.descriptor)
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
        try:
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
        except asyncio.CancelledError:
            await self.lifecycle.cancelled(request=request, adapter=self.descriptor)
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
