"""Centralized Tool Gateway contracts and fail-closed execution boundary.

Adapters and model runtimes describe tool intents; only this gateway resolves,
authorizes, executes, times out, cancels and audits tool implementations.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, Protocol
from uuid import uuid4

from agent_platform.contracts import RunContext
from agent_platform.observability.trace_spans import trace_span

_TOOL_ID = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")


class ToolGatewayError(RuntimeError):
    """Base class for rejected or failed Gateway operations."""


class ToolAuthenticationError(ToolGatewayError):
    pass


class ToolAuthorizationError(ToolGatewayError):
    pass


class ToolRunBindingError(ToolGatewayError):
    pass


class ToolNotFoundError(ToolGatewayError):
    pass


class ToolExecutionTimeout(ToolGatewayError):
    pass


class ToolExecutionCancelled(ToolGatewayError):
    pass


@dataclass(frozen=True)
class ToolPrincipal:
    """Authenticated caller identity; credentials must never be placed in metadata."""
    subject_id: str
    tenant_id: str
    agent_id: str
    scopes: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.subject_id.strip() or not self.tenant_id.strip() or not self.agent_id.strip():
            raise ValueError("principal subject, tenant and agent are required")


@dataclass(frozen=True)
class ToolRunBinding:
    tenant_id: str
    run_id: str
    agent_id: str
    project_id: str | None = None
    allowed_tools: frozenset[str] = frozenset()
    active: bool = True


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    handler: Callable[[Mapping[str, Any], "ToolExecutionContext"], Awaitable[Any]]
    required_scopes: frozenset[str] = frozenset()
    timeout_seconds: float = 30.0
    description: str = ""

    def __post_init__(self) -> None:
        if not _TOOL_ID.fullmatch(self.name):
            raise ValueError("tool name must be a stable lowercase identifier")
        if not callable(self.handler):
            raise TypeError("tool handler must be callable")
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, (int, float)) or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("tool timeout must be a finite positive number")


@dataclass(frozen=True)
class ToolExecutionContext:
    tenant_id: str
    run_id: str
    agent_id: str
    project_id: str | None
    principal: ToolPrincipal
    call_id: str
    correlation_id: str


@dataclass(frozen=True)
class ToolAuditEvent:
    event_type: str
    tenant_id: str
    run_id: str
    agent_id: str
    actor_id: str
    tool_name: str
    call_id: str
    outcome: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: int | None = None
    error_type: str | None = None
    arguments_sha256: str | None = None
    correlation_id: str | None = None


class ToolAuthenticator(Protocol):
    async def authenticate(self, credential: str) -> ToolPrincipal | None: ...


class ToolAuthorizer(Protocol):
    async def authorize(self, *, principal: ToolPrincipal, run: ToolRunBinding, tool: ToolDefinition) -> bool: ...


class ToolRunRegistry(Protocol):
    async def get_binding(self, *, tenant_id: str, run_id: str) -> ToolRunBinding | None: ...


class ToolAuditSink(Protocol):
    async def append(self, event: ToolAuditEvent) -> None: ...


class InMemoryToolRunRegistry:
    """Small reference registry; production deployments should use durable storage."""
    def __init__(self) -> None:
        self._bindings: dict[tuple[str, str], ToolRunBinding] = {}

    def bind(self, binding: ToolRunBinding) -> None:
        self._bindings[(binding.tenant_id, binding.run_id)] = binding

    async def get_binding(self, *, tenant_id: str, run_id: str) -> ToolRunBinding | None:
        return self._bindings.get((tenant_id, run_id))


class ScopeToolAuthorizer:
    """Default least-privilege policy: run allowlist AND every required scope."""
    async def authorize(self, *, principal: ToolPrincipal, run: ToolRunBinding, tool: ToolDefinition) -> bool:
        return (
            tool.name in run.allowed_tools
            and tool.required_scopes.issubset(principal.scopes)
        )


class InMemoryToolAuditSink:
    def __init__(self) -> None:
        self.events: list[ToolAuditEvent] = []

    async def append(self, event: ToolAuditEvent) -> None:
        self.events.append(event)


class ToolGateway:
    """Only supported path from model/agent tool intents to executable tools."""

    def __init__(
        self, *, authenticator: ToolAuthenticator, run_registry: ToolRunRegistry,
        audit_sink: ToolAuditSink, authorizer: ToolAuthorizer | None = None,
        max_timeout_seconds: float = 300.0,
        trace_exporter: Callable[[Mapping[str, Any]], Any] | None = None,
    ) -> None:
        if max_timeout_seconds <= 0 or not math.isfinite(max_timeout_seconds):
            raise ValueError("max_timeout_seconds must be finite and positive")
        self.authenticator = authenticator
        self.run_registry = run_registry
        self.audit_sink = audit_sink
        self.authorizer = authorizer or ScopeToolAuthorizer()
        self.max_timeout_seconds = max_timeout_seconds
        if trace_exporter is not None and not callable(trace_exporter):
            raise TypeError("trace_exporter must be callable")
        self.trace_exporter = trace_exporter
        self._tools: dict[str, ToolDefinition] = {}
        self._active: dict[tuple[str, str, str], asyncio.Task] = {}
        self._cancelled: set[tuple[str, str, str]] = set()

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._tools:
            raise ValueError(f"tool already registered: {definition.name}")
        self._tools[definition.name] = definition

    async def invoke(
        self, *, credential: str, run_context: RunContext, tool_name: str,
        arguments: Mapping[str, Any], call_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> Any:
        call_id = call_id or str(uuid4())
        if not isinstance(call_id, str) or not call_id.strip():
            raise ValueError("call_id must not be empty")
        if not isinstance(arguments, Mapping):
            raise ValueError("tool arguments must be an object")
        started = time.perf_counter()
        digest = hashlib.sha256(json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
        outcome, error_type = "rejected", None
        definition = self._tools.get(tool_name)
        try:
            principal = await self.authenticator.authenticate(credential)
            if principal is None:
                raise ToolAuthenticationError("tool caller authentication failed")
            if (principal.tenant_id, principal.agent_id) != (run_context.tenant_id, run_context.agent_id):
                raise ToolRunBindingError("authenticated principal does not match Run tenant/agent")
            binding = await self.run_registry.get_binding(tenant_id=run_context.tenant_id, run_id=run_context.run_id)
            if binding is None or not binding.active:
                raise ToolRunBindingError("Run is missing, inactive or not bound")
            if (
                binding.tenant_id != run_context.tenant_id
                or binding.run_id != run_context.run_id
                or binding.agent_id != run_context.agent_id
                or binding.project_id != run_context.project_id
            ):
                raise ToolRunBindingError("Run binding scope mismatch")
            if definition is None:
                raise ToolNotFoundError(f"unknown tool: {tool_name}")
            if not await self.authorizer.authorize(principal=principal, run=binding, tool=definition):
                raise ToolAuthorizationError("tool is not authorized for this principal and Run")
            requested_timeout = definition.timeout_seconds if timeout_seconds is None else timeout_seconds
            if isinstance(requested_timeout, bool) or not isinstance(requested_timeout, (int, float)) or not math.isfinite(requested_timeout) or requested_timeout <= 0:
                raise ValueError("timeout_seconds must be finite and positive")
            effective_timeout = min(float(requested_timeout), definition.timeout_seconds, self.max_timeout_seconds)
            key = (run_context.tenant_id, run_context.run_id, call_id)
            if key in self._active:
                raise ToolGatewayError("duplicate active tool call_id")
            context = ToolExecutionContext(
                tenant_id=run_context.tenant_id, run_id=run_context.run_id,
                agent_id=run_context.agent_id, project_id=run_context.project_id,
                principal=principal, call_id=call_id, correlation_id=run_context.correlation_id,
            )
            async def execute_with_trace() -> Any:
                with trace_span(
                    f"tool.{tool_name}",
                    component="tool",
                    tenant_id=run_context.tenant_id,
                    run_id=run_context.run_id,
                    agent_id=run_context.agent_id,
                    tool_name=tool_name,
                    correlation_id=run_context.correlation_id,
                    exporter=self.trace_exporter,
                ):
                    return await definition.handler(dict(arguments), context)

            task = asyncio.create_task(execute_with_trace())
            self._active[key] = task
            try:
                result = await asyncio.wait_for(task, timeout=effective_timeout)
            except asyncio.TimeoutError as exc:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                outcome, error_type = "timeout", "ToolExecutionTimeout"
                raise ToolExecutionTimeout(f"tool {tool_name} exceeded {effective_timeout:g}s deadline") from exc
            except asyncio.CancelledError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                outcome, error_type = "cancelled", "ToolExecutionCancelled"
                raise
            finally:
                self._active.pop(key, None)
                self._cancelled.discard(key)
            outcome = "success"
            return result
        except asyncio.CancelledError:
            if error_type is None:
                outcome, error_type = "cancelled", "ToolExecutionCancelled"
            raise
        except Exception as exc:
            error_type = error_type or type(exc).__name__
            raise
        finally:
            await self.audit_sink.append(ToolAuditEvent(
                event_type="tool.invocation", tenant_id=run_context.tenant_id,
                run_id=run_context.run_id, agent_id=run_context.agent_id,
                actor_id=principal.subject_id if "principal" in locals() and principal else "anonymous",
                tool_name=tool_name, call_id=call_id, outcome=outcome,
                duration_ms=round((time.perf_counter() - started) * 1000),
                error_type=error_type, arguments_sha256=digest,
                correlation_id=run_context.correlation_id,
            ))

    async def cancel_run(self, *, tenant_id: str, run_id: str) -> int:
        """Cancel every active tool invocation bound to the specified tenant/Run."""
        binding = await self.run_registry.get_binding(tenant_id=tenant_id, run_id=run_id)
        if binding is None or not binding.active:
            raise ToolRunBindingError("cannot cancel tools for an unbound or inactive Run")
        tasks = [
            (key, task) for key, task in tuple(self._active.items())
            if key[:2] == (tenant_id, run_id) and not task.done()
        ]
        for key, task in tasks:
            self._cancelled.add(key)
            task.cancel()
        if tasks:
            await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
        return len(tasks)
