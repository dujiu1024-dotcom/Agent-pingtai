"""Registry-backed workbench task submission and durable Run worker.

The workbench owns task persistence and authorization context. Agent-specific
LangGraph construction remains a trusted host extension; execution always uses
the platform Run lifecycle, immutable registry snapshot and worker lease.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any
from uuid import uuid4

from agent_platform.control_plane.registry import RegistryService
from agent_platform.runtime.execution import LangGraphRunExecutor
from agent_platform.runtime.lifecycle import RunLifecycleManager, RunStatus, WorkerLeaseLost


class WorkbenchTaskError(ValueError):
    """Invalid task request or Agent runtime configuration."""


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "hex") and value.__class__.__name__ == "UUID":
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(child) for child in value]
    return value


def _task_view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "task_id": str(row["task_id"]),
        "run_id": str(row["run_id"]),
        "agent_id": row["agent_id"],
        "project_id": row.get("project_id"),
        "session_id": row.get("session_id"),
        "status": row["status"],
        "input": _json_value(row.get("input") or {}),
        "output": _json_value(row.get("output")),
        "usage": _json_value(row.get("usage") or {}),
        "trace_id": row.get("trace_id"),
        "created_at": _json_value(row.get("created_at")),
        "updated_at": _json_value(row.get("updated_at")),
        "started_at": _json_value(row.get("started_at")),
        "finished_at": _json_value(row.get("finished_at")),
        "checkpoint_ref": row.get("checkpoint_ref"),
        "last_error": row.get("last_error"),
        "registry_snapshot_digest": row.get("registry_snapshot_digest"),
    }


class RegistryBackedTaskCenterService:
    """Concrete TaskCenterService using registry versions and durable Run storage."""

    def __init__(
        self, *, tenant_id: str, registry: RegistryService,
        snapshots: Any, run_store: Any, lifecycle: RunLifecycleManager,
    ) -> None:
        if not tenant_id.strip():
            raise ValueError("tenant_id is required")
        repository_tenant = getattr(registry.repository, "tenant_id", tenant_id)
        if repository_tenant != tenant_id:
            raise ValueError("registry repository tenant mismatch")
        self.tenant_id = tenant_id
        self.registry = registry
        self.repository = registry.repository
        self.snapshots = snapshots
        self.run_store = run_store
        self.lifecycle = lifecycle

    def _check_tenant(self, tenant_id: str) -> None:
        if tenant_id != self.tenant_id:
            raise PermissionError("task service tenant mismatch")

    @staticmethod
    def _string_list(value: Any, field: str) -> tuple[str, ...]:
        if value is None:
            return ()
        if (
            not isinstance(value, list)
            or any(not isinstance(item, str) or not item.strip() for item in value)
            or len(set(value)) != len(value)
        ):
            raise WorkbenchTaskError(f"Agent {field} must be a unique string array")
        return tuple(value)

    async def _runtime_references(
        self, agent_id: str,
    ) -> tuple[Any, str, int, str | None, tuple[str, ...], tuple[str, ...]]:
        agent = await self.registry.resolve_active(
            tenant_id=self.tenant_id, kind="agent", item_id=agent_id,
        )
        definition = agent.definition
        instructions = definition.get("instructions")
        template_id = definition.get("model_template_id")
        if not isinstance(instructions, str) or not instructions.strip():
            raise WorkbenchTaskError("published Agent must define non-empty instructions")
        if not isinstance(template_id, str) or not template_id.strip():
            raise WorkbenchTaskError("published Agent must define model_template_id")

        template = await self.registry.resolve_active(
            tenant_id=self.tenant_id, kind="model_template", item_id=template_id,
        )
        template_definition = template.definition
        provider_id = template_definition.get("provider_id")
        provider_version = template_definition.get("provider_version")
        if (
            not isinstance(provider_id, str) or not provider_id.strip()
            or not isinstance(provider_version, int) or provider_version < 1
        ):
            raise WorkbenchTaskError("active model template must pin provider_id and provider_version")
        provider = await self.registry.repository.get(
            self.tenant_id, "model_provider", provider_id, provider_version,
        )
        if provider is None or provider.status != "published":
            raise WorkbenchTaskError("model template references a missing or unpublished provider version")
        active_provider = await self.registry.resolve_active(
            tenant_id=self.tenant_id, kind="model_provider", item_id=provider_id,
        )
        if active_provider.version != provider_version:
            raise WorkbenchTaskError(
                "model provider active version differs from the version pinned by the template"
            )

        tool_ids = self._string_list(definition.get("tool_ids", []), "tool_ids")
        skill_ids = self._string_list(definition.get("skill_ids", []), "skill_ids")
        workflow_id = definition.get("workflow_id")
        if workflow_id is not None and (
            not isinstance(workflow_id, str) or not workflow_id.strip()
        ):
            raise WorkbenchTaskError("workflow_id must be a non-empty string when supplied")

        for kind, item_ids in (("tool", tool_ids), ("skill", skill_ids)):
            for item_id in item_ids:
                await self.registry.resolve_active(
                    tenant_id=self.tenant_id, kind=kind, item_id=item_id,
                )
        if workflow_id:
            await self.registry.resolve_active(
                tenant_id=self.tenant_id, kind="workflow", item_id=workflow_id,
            )

        return agent, template_id, template.version, workflow_id, tool_ids, skill_ids

    async def list_tasks(
        self, *, tenant_id: str, actor_id: str, limit: int = 100,
    ) -> list[Mapping[str, Any]]:
        self._check_tenant(tenant_id)
        if not actor_id.strip():
            raise PermissionError("authenticated actor is required")
        rows = await self.run_store.list_task_records(
            tenant_id, actor_id, limit=limit,
        )
        return [_task_view(row) for row in rows]

    async def submit_task(
        self, *, tenant_id: str, actor_id: str, agent_id: str,
        project_id: str | None, session_id: str | None,
        input: Mapping[str, Any], idempotency_key: str | None = None,
    ) -> Mapping[str, Any]:
        self._check_tenant(tenant_id)
        if not actor_id.strip():
            raise PermissionError("authenticated actor is required")
        if not isinstance(input, Mapping) or not input:
            raise WorkbenchTaskError("task input must be a non-empty object")
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str)
            or not idempotency_key.strip() or len(idempotency_key) > 200
        ):
            raise WorkbenchTaskError("idempotency_key must contain 1 to 200 characters")

        request_body = {
            "tenant_id": tenant_id, "actor_id": actor_id, "agent_id": agent_id,
            "project_id": project_id, "session_id": session_id, "input": dict(input),
        }
        canonical = json.dumps(
            request_body, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if idempotency_key is not None:
            existing = await self.run_store.get_task_by_idempotency(
                tenant_id, actor_id, idempotency_key,
            )
            if existing is not None:
                if existing.get("request_digest") != digest:
                    raise WorkbenchTaskError(
                        "idempotency_key was already used with a different task request"
                    )
                return _task_view(existing)

        agent, template_id, template_version, workflow_id, tool_ids, skill_ids = (
            await self._runtime_references(agent_id)
        )
        template = await self.registry.resolve_active(
            tenant_id=tenant_id, kind="model_template", item_id=template_id,
        )
        run_id = str(uuid4())
        created = await self.run_store.create_task_run(
            tenant_id=tenant_id, run_id=run_id, actor_id=actor_id,
            agent_id=agent_id, template_id=template_id,
            template_version=template_version, input=dict(input),
            project_id=project_id, session_id=session_id,
            workflow_id=workflow_id, idempotency_key=idempotency_key,
            request_digest=digest,
        )
        if not created:
            # The unique idempotency key resolves to the original Run and snapshot.
            existing = await self.run_store.get_task_by_idempotency(
                tenant_id, actor_id, idempotency_key,
            )
            if existing is None:
                raise RuntimeError("idempotent task record disappeared")
            return _task_view(existing)

        references = [
            ("agent", agent_id),
            ("model_template", template_id),
            ("model_provider", str(template.definition["provider_id"])),
            *(("tool", item_id) for item_id in tool_ids),
            *(("skill", item_id) for item_id in skill_ids),
        ]
        if workflow_id:
            references.append(("workflow", workflow_id))
        try:
            await self.snapshots.pin(
                tenant_id=tenant_id, run_id=run_id, references=references,
            )
        except Exception as exc:
            current = await self.run_store.get(tenant_id, run_id)
            if current is not None and current.status == RunStatus.QUEUED:
                await self.lifecycle.finish(
                    tenant_id, run_id, RunStatus.FAILED,
                    error=f"registry snapshot pin failed: {str(exc)[:400]}",
                )
            raise WorkbenchTaskError("could not pin the Agent runtime registry snapshot") from exc

        record = await self.run_store.get_task_record(
            tenant_id, run_id, actor_id=actor_id,
        )
        if record is None:
            raise RuntimeError("created task record could not be read")
        return _task_view(record)

    async def get_task(
        self, *, tenant_id: str, task_id: str, actor_id: str,
    ) -> Mapping[str, Any] | None:
        self._check_tenant(tenant_id)
        row = await self.run_store.get_task_record(
            tenant_id, task_id, actor_id=actor_id,
        )
        return _task_view(row) if row is not None else None

    async def cancel_task(
        self, *, tenant_id: str, task_id: str, actor_id: str,
    ) -> Mapping[str, Any] | None:
        self._check_tenant(tenant_id)
        row = await self.run_store.get_task_record(
            tenant_id, task_id, actor_id=actor_id,
        )
        if row is None:
            return None
        await self.lifecycle.cancel(tenant_id, str(row["run_id"]))
        updated = await self.run_store.get_task_record(
            tenant_id, task_id, actor_id=actor_id,
        )
        return _task_view(updated) if updated is not None else None


def create_registry_backed_task_service_resolver(
    *, registry_resolver: Callable[[Any], Any],
    snapshot_resolver: Callable[[Any], Any],
    run_store: Any,
    lifecycle_resolver: Callable[[Any], Any],
) -> Callable[[Any], Awaitable[RegistryBackedTaskCenterService]]:
    """Compose the concrete task service from the platform's tenant-bound ports."""
    async def resolve(principal: Any) -> RegistryBackedTaskCenterService:
        registry = registry_resolver(principal)
        snapshots = snapshot_resolver(principal)
        lifecycle = lifecycle_resolver(principal)
        if inspect.isawaitable(registry):
            registry = await registry
        if inspect.isawaitable(snapshots):
            snapshots = await snapshots
        if inspect.isawaitable(lifecycle):
            lifecycle = await lifecycle
        if lifecycle.store is not run_store:
            raise ValueError("task service lifecycle must share the durable RunStateStore")
        return RegistryBackedTaskCenterService(
            tenant_id=principal.tenant_id, registry=registry,
            snapshots=snapshots, run_store=run_store, lifecycle=lifecycle,
        )
    return resolve


class RegistryLangGraphExecutorResolver:
    """Resolve a Run-pinned Agent definition into the platform LangGraph executor.

    Only the trusted graph factory is host supplied. Registry resolution,
    snapshot validation, lifecycle binding and worker lease identity are enforced
    here rather than repeated in every deployment composition.
    """

    def __init__(
        self, *, snapshots: Any,
        lifecycle_resolver: Callable[[str], Any],
        runtime_provider_resolver: Callable[[str], Any],
        graph_factory: Callable[..., Any],
    ) -> None:
        self.snapshots = snapshots
        self.lifecycle_resolver = lifecycle_resolver
        self.runtime_provider_resolver = runtime_provider_resolver
        self.graph_factory = graph_factory

    async def _resolve(self, callback: Callable[..., Any], *args: Any) -> Any:
        value = callback(*args)
        return await value if inspect.isawaitable(value) else value

    async def __call__(
        self, record: Mapping[str, Any], worker_id: str,
    ) -> LangGraphRunExecutor:
        tenant_id = str(record.get("tenant_id") or "")
        run_id = str(record.get("run_id") or "")
        agent_id = str(record.get("agent_id") or "")
        template_id = str(record.get("template_id") or "")
        if not all((tenant_id, run_id, agent_id, template_id, worker_id)):
            raise ValueError("task record is missing tenant, Run, Agent, template or worker identity")
        snapshot = await self.snapshots.get(
            tenant_id=tenant_id, run_id=run_id,
        )
        if snapshot.tenant_id != tenant_id or snapshot.run_id != run_id:
            raise PermissionError("Run registry snapshot scope mismatch")
        matches = [
            item for item in snapshot.entries
            if item.get("kind") == "agent" and item.get("item_id") == agent_id
        ]
        if len(matches) != 1 or not isinstance(matches[0].get("definition"), Mapping):
            raise ValueError("Run snapshot must contain exactly one immutable Agent definition")
        template_matches = [
            item for item in snapshot.entries
            if item.get("kind") == "model_template"
            and item.get("item_id") == template_id
        ]
        if len(template_matches) != 1:
            raise ValueError("Run snapshot does not contain the selected model template")
        agent_definition = dict(matches[0]["definition"])
        tool_ids = agent_definition.get("tool_ids", [])
        if not isinstance(tool_ids, list) or any(
            not isinstance(item, str) or not item for item in tool_ids
        ):
            raise ValueError("Run-pinned Agent tool_ids must be a string array")
        lifecycle = await self._resolve(self.lifecycle_resolver, tenant_id)
        runtime_provider = await self._resolve(
            self.runtime_provider_resolver, tenant_id,
        )
        if lifecycle.store is None or runtime_provider is None:
            raise RuntimeError("durable Run lifecycle and registry runtime provider are required")
        graph = await self._resolve(
            self.graph_factory,
            agent_definition=agent_definition,
            snapshot=snapshot,
            task_record=record,
            runtime_provider=runtime_provider,
        )
        return LangGraphRunExecutor(
            graph=graph, lifecycle=lifecycle, worker_id=worker_id,
            registry_snapshot_service=self.snapshots,
            registry_runtime_provider=runtime_provider,
            model_template_id=template_id,
            required_tools=tuple(tool_ids),
        )


class RunExecutionWorker:
    """Lease-claiming worker for queued, interrupted and remotely cancelled Runs."""

    def __init__(
        self, *, run_store: Any, executor_resolver: Callable[..., Any],
        lifecycle_resolver: Callable[[str], Any], worker_id: str,
        poll_interval: float = 1.0, batch_size: int = 10, lease_seconds: int = 60,
    ) -> None:
        if not worker_id.strip() or poll_interval <= 0:
            raise ValueError("worker_id and positive poll_interval are required")
        if not 1 <= batch_size <= 200 or lease_seconds < 5:
            raise ValueError("invalid worker batch or lease configuration")
        self.run_store = run_store
        self.executor_resolver = executor_resolver
        self.lifecycle_resolver = lifecycle_resolver
        self.worker_id = worker_id
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self.lease_seconds = lease_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[Any] | None = None

    async def _resolve(self, callback: Callable[..., Any], *args: Any) -> Any:
        value = callback(*args)
        return await value if inspect.isawaitable(value) else value

    async def run_once(self) -> dict[str, int]:
        claimed = await self.run_store.claim_workbench_tasks(
            self.worker_id, limit=self.batch_size,
            lease_seconds=self.lease_seconds,
        )

        async def process(snapshot: Any) -> str:
            tenant_id, run_id = snapshot.tenant_id, snapshot.run_id
            record = await self.run_store.get_task_record(tenant_id, run_id)
            if record is None:
                await self.run_store.release_lease(tenant_id, run_id, self.worker_id)
                return "skipped"
            try:
                resolved = await self._resolve(
                    self.executor_resolver, record, self.worker_id,
                )
                if not isinstance(resolved, LangGraphRunExecutor):
                    raise TypeError("executor_resolver must return LangGraphRunExecutor")
                if resolved.worker_id != self.worker_id:
                    raise ValueError("resolved executor must use the claiming worker_id")
                if resolved.lifecycle.store is not self.run_store:
                    raise ValueError("resolved executor must share the worker RunStateStore")
                if snapshot.status == RunStatus.QUEUED:
                    result = await resolved.execute(
                        tenant_id=tenant_id, run_id=run_id, input=record.get("input") or {},
                    )
                else:
                    result = await resolved.resume_claimed(
                        snapshot=snapshot, input=record.get("input") or {},
                    )
                current = await self.run_store.get(tenant_id, run_id)
                if current is not None and current.status == RunStatus.SUCCEEDED and result is not None:
                    usage = result.get("usage", {}) if isinstance(result, Mapping) else {}
                    trace_id = result.get("trace_id") if isinstance(result, Mapping) else None
                    safe_result = _json_value(result)
                    try:
                        json.dumps(safe_result, ensure_ascii=False, allow_nan=False)
                    except (TypeError, ValueError):
                        safe_result = {"text": str(result)}
                    await self.run_store.save_output(
                        tenant_id, run_id, safe_result,
                        usage=usage if isinstance(usage, Mapping) else {},
                        trace_id=trace_id if isinstance(trace_id, str) else None,
                    )
                current = await self.run_store.get(tenant_id, run_id)
                if current and current.status == RunStatus.SUCCEEDED:
                    return "succeeded"
                if current and current.status == RunStatus.CANCELLED:
                    return "cancelled"
                if current and current.status in {RunStatus.FAILED, RunStatus.TIMED_OUT}:
                    return "failed"
                return "skipped"
            except asyncio.CancelledError:
                raise
            except WorkerLeaseLost:
                # Another worker owns the lease now; never mark its Run failed.
                return "skipped"
            except Exception as exc:
                lifecycle = await self._resolve(self.lifecycle_resolver, tenant_id)
                current = await self.run_store.get(tenant_id, run_id)
                if current and current.status == RunStatus.CANCELLING:
                    await lifecycle.finish(tenant_id, run_id, RunStatus.CANCELLED)
                elif current and current.status not in {
                    RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.TIMED_OUT,
                }:
                    await lifecycle.finish(
                        tenant_id, run_id, RunStatus.FAILED,
                        error=f"workbench worker failed: {str(exc)[:400]}",
                    )
                return "failed"
            finally:
                await self.run_store.release_lease(tenant_id, run_id, self.worker_id)

        outcomes = await asyncio.gather(*(process(snapshot) for snapshot in claimed))
        counts = {
            "claimed": len(claimed), "succeeded": 0, "failed": 0,
            "cancelled": 0, "skipped": 0,
        }
        for outcome in outcomes:
            counts[outcome] += 1
        return counts

    async def run_forever(self) -> None:
        self._stop.clear()
        while not self._stop.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A transient database outage must not terminate the worker process.
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                continue

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self.run_forever(), name=f"workbench-run-worker-{self.worker_id}")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
