"""Ports implemented by open-source Harness and model-provider adapters."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from agent_platform.contracts import ModelTemplateRef, RunContext



_SUPPORTED_MODALITIES = frozenset({"text", "image", "pdf", "audio", "video"})


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

    def __post_init__(self) -> None:
        if not self.input_modalities.issubset(_SUPPORTED_MODALITIES):
            raise ValueError("Harness declares an unsupported input modality")
        if not self.output_modalities.issubset(_SUPPORTED_MODALITIES):
            raise ValueError("Harness declares an unsupported output modality")


class UnsupportedHarnessCapability(RuntimeError):
    """Raised when a request requires a modality not declared by its adapter."""


class CapabilityAwareHarnessAdapter:
    """Fail-closed wrapper that checks declared input/output modalities."""

    def __init__(self, *, adapter: Any, capabilities: HarnessCapabilities) -> None:
        if not callable(getattr(adapter, "run", None)):
            raise TypeError("adapter must provide an async run(request) method")
        self.adapter = adapter
        self.capabilities = capabilities

    async def run(self, request: "HarnessRequest") -> "HarnessResult":
        requested_inputs = {"text"} if request.input_text else set()
        requested_inputs.update(item.modality for item in request.media_inputs)
        unsupported = requested_inputs - self.capabilities.input_modalities
        if unsupported:
            raise UnsupportedHarnessCapability(
                "Harness does not declare input modalities: " + ", ".join(sorted(unsupported))
            )
        result = await self.adapter.run(request)
        produced_outputs = {"text"} if result.output_text else set()
        produced_outputs.update(item.modality for item in result.media_outputs)
        unsupported_outputs = produced_outputs - self.capabilities.output_modalities
        if unsupported_outputs:
            raise UnsupportedHarnessCapability(
                "Harness does not declare output modalities: "
                + ", ".join(sorted(unsupported_outputs))
            )
        return result

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


class HarnessAdapter(Protocol):
    """Adapter around an existing agent execution engine; no custom loop here."""

    async def run(self, request: HarnessRequest) -> HarnessResult:
        """Execute one supervised agent turn and return a normalized result."""
        ...


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
