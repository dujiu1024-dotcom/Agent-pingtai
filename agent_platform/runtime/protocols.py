"""Ports implemented by open-source Harness and model-provider adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from agent_platform.contracts import ModelTemplateRef, RunContext


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


@dataclass(frozen=True)
class HarnessResult:
    output_text: str
    tool_calls: tuple[ToolCall, ...] = ()
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str = "completed"
    trace_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


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
