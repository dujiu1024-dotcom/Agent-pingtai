"""Durable child-Run execution through the registry-backed task center.

This adapter persists a child Run before dispatch, reuses the platform worker,
and returns only after the durable Run reaches a terminal state.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections.abc import Callable, Mapping
from typing import Any

from agent_platform.runtime.protocols import HarnessRequest, HarnessResult, MediaOutput


class TaskCenterChildRunExecutor:
    """Implement ChildRunExecutor with durable task submission and worker execution.

    The supplied worker must share the task service's PostgreSQL RunStateStore.
    It may be the platform worker itself or a separately deployed child worker.
    """

    def __init__(
        self,
        *,
        task_service: Any,
        worker: Any | None = None,
        actor_resolver: Callable[[Any], Any],
        poll_interval: float = 0.1,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        if worker is not None and task_service.run_store is not worker.run_store:
            raise ValueError("child task service and worker must share the durable RunStateStore")
        self.task_service = task_service
        self.worker = worker
        self.actor_resolver = actor_resolver
        self.poll_interval = poll_interval

    def bind_worker(self, worker: Any) -> None:
        """Bind the worker after construction to break the graph/worker composition cycle."""
        if self.task_service.run_store is not worker.run_store:
            raise ValueError("child task service and worker must share the durable RunStateStore")
        if not callable(getattr(worker, "run_once", None)):
            raise TypeError("child worker must provide run_once()")
        self.worker = worker

    async def _resolve_actor(self, parent_context: Any) -> str:
        actor = self.actor_resolver(parent_context)
        if inspect.isawaitable(actor):
            actor = await actor
        if not isinstance(actor, str) or not actor.strip():
            raise PermissionError("child Run requires the authenticated parent actor")
        return actor

    async def _validate_pinned_runtime(self, request: HarnessRequest) -> None:
        registry = self.task_service.registry
        tenant_id = request.context.tenant_id
        agent = await registry.resolve_active(
            tenant_id=tenant_id, kind="agent", item_id=request.context.agent_id,
        )
        definition = agent.definition
        if not isinstance(definition, Mapping):
            raise ValueError("published delegated Agent definition must be an object")
        template_id = definition.get("model_template_id")
        if template_id != request.context.model_template.template_id:
            raise PermissionError("delegated Agent model template differs from the parent Run template")
        template = await registry.resolve_active(
            tenant_id=tenant_id, kind="model_template", item_id=template_id,
        )
        if template.version != request.context.model_template.version:
            raise PermissionError("delegated Agent model template version differs from the parent Run")
        declared_tools = self.task_service._string_list(
            definition.get("tool_ids", []), "tool_ids",
        )
        if not set(request.allowed_tools).issubset(declared_tools):
            raise PermissionError("delegated tools exceed the published Agent tool allowlist")

    @staticmethod
    def _task_input(request: HarnessRequest, parent_context: Any) -> dict[str, Any]:
        return {
            "instructions": request.instructions,
            "input_text": request.input_text,
            "media_inputs": [
                {
                    "modality": media.modality,
                    "resource_id": media.resource_id,
                    "mime_type": media.mime_type,
                    "size_bytes": media.size_bytes,
                    "sha256": media.sha256,
                    "filename": media.filename,
                }
                for media in request.media_inputs
            ],
            "allowed_tools": list(request.allowed_tools),
            "output_schema": request.output_schema,
            "metadata": {
                **dict(request.metadata),
                "parent_run_id": parent_context.run_id,
                "parent_agent_id": parent_context.agent_id,
            },
            "context": {
                "tenant_id": request.context.tenant_id,
                "run_id": request.context.run_id,
                "agent_id": request.context.agent_id,
                "task_id": request.context.task_id,
                "project_id": request.context.project_id,
                "correlation_id": request.context.correlation_id,
                "model_template_id": request.context.model_template.template_id,
                "model_template_version": request.context.model_template.version,
                "metadata": dict(request.context.metadata),
            },
        }

    async def execute_child(
        self,
        *,
        request: HarnessRequest,
        parent_context: Any,
        idempotency_key: str,
    ) -> HarnessResult:
        if request.context.tenant_id != parent_context.tenant_id:
            raise PermissionError("child Run tenant must match its parent")
        if request.context.metadata.get("parent_run_id") != parent_context.run_id:
            raise PermissionError("child Run must carry the authenticated parent Run identity")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError("child Run idempotency key is required")
        await self._validate_pinned_runtime(request)
        actor_id = await self._resolve_actor(parent_context)
        task_input = self._task_input(request, parent_context)
        durable_idempotency_key = "child-run:" + hashlib.sha256(
            idempotency_key.encode("utf-8")
        ).hexdigest()
        submitted = await self.task_service.submit_task(
            tenant_id=parent_context.tenant_id,
            actor_id=actor_id,
            agent_id=request.context.agent_id,
            project_id=request.context.project_id,
            session_id=parent_context.metadata.get("session_id"),
            input=task_input,
            idempotency_key=durable_idempotency_key,
            run_id=request.context.run_id,
        )
        if submitted.get("run_id") != request.context.run_id:
            raise RuntimeError("idempotent child submission resolved to a different Run ID")
        if (
            submitted.get("template_id") != request.context.model_template.template_id
            or submitted.get("template_version") != request.context.model_template.version
        ):
            await self.task_service.cancel_task(
                tenant_id=parent_context.tenant_id,
                task_id=request.context.run_id,
                actor_id=actor_id,
            )
            raise PermissionError("persisted child Run changed its pinned model template version")

        try:
            while True:
                task = await self.task_service.get_task(
                    tenant_id=parent_context.tenant_id,
                    task_id=request.context.run_id,
                    actor_id=actor_id,
                )
                if task is None:
                    raise RuntimeError("durable child Run disappeared before completion")
                status = task.get("status")
                if status == "succeeded":
                    output = task.get("output")
                    if isinstance(output, Mapping):
                        output_text = output.get("text", "")
                        if not isinstance(output_text, str):
                            raise ValueError("child Run output text must be a string")
                        raw_media_outputs = output.get("media_outputs", ())
                        if not isinstance(raw_media_outputs, (list, tuple)):
                            raise ValueError("child Run media_outputs must be an array")
                        media_outputs = []
                        for item in raw_media_outputs:
                            if not isinstance(item, Mapping):
                                raise ValueError("child Run media output must be an object")
                            media_outputs.append(MediaOutput(
                                modality=item.get("modality"),
                                resource_id=item.get("resource_id"),
                                mime_type=item.get("mime_type"),
                                size_bytes=item.get("size_bytes"),
                                sha256=item.get("sha256"),
                                filename=item.get("filename"),
                            ))
                    elif isinstance(output, str):
                        output_text = output
                        media_outputs = []
                    else:
                        output_text = json.dumps(
                            output if output is not None else {},
                            ensure_ascii=False, sort_keys=True, default=str,
                        )
                        media_outputs = []
                    usage = task.get("usage") or {}
                    return HarnessResult(
                        output_text=output_text,
                        media_outputs=tuple(media_outputs),
                        usage=dict(usage) if isinstance(usage, Mapping) else {},
                        trace_id=task.get("trace_id") if isinstance(task.get("trace_id"), str) else None,
                        metadata={"child_run_id": request.context.run_id},
                    )
                if status in {"failed", "cancelled", "timed_out"}:
                    raise RuntimeError(
                        f"child Run {request.context.run_id} ended with status {status}: "
                        f"{task.get('last_error') or 'no error detail'}"
                    )
                # A worker may be deployed independently from the parent
                # process. In that topology, keep observing the durable Run
                # instead of treating the absent local worker as a failure.
                if self.worker is not None:
                    await self.worker.run_once()
                await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            # SupervisedDelegationService applies the parent deadline with
            # wait_for(); cancellation must stop the persisted child Run too.
            try:
                await self.task_service.cancel_task(
                    tenant_id=parent_context.tenant_id,
                    task_id=request.context.run_id,
                    actor_id=actor_id,
                )
            finally:
                raise
        except Exception:
            # A failed worker poll must not leave an unobserved child Run queued.
            await self.task_service.cancel_task(
                tenant_id=parent_context.tenant_id,
                task_id=request.context.run_id,
                actor_id=actor_id,
            )
            raise
