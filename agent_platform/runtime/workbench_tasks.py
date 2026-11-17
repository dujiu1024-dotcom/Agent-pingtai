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
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from agent_platform.control_plane.registry import RegistryService
from agent_platform.quotas.service import BudgetExceeded
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
        "agent_version": row.get("agent_version"),
        "template_id": row.get("template_id"),
        "template_version": row.get("template_version"),
        "project_id": row.get("project_id"),
        "session_id": row.get("session_id"),
        "status": row["status"],
        "priority": row.get("priority", 100),
        "retry_of_run_id": str(row["retry_of_run_id"]) if row.get("retry_of_run_id") else None,
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
        "dispatch_ready": bool(row.get("dispatch_ready", False)),
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
    ) -> tuple[Any, Any, Any, tuple[Any, ...], tuple[Any, ...], Any | None]:
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

        tools = tuple(
            [
                await self.registry.resolve_active(
                    tenant_id=self.tenant_id, kind="tool", item_id=item_id,
                )
                for item_id in tool_ids
            ]
        )
        skills = tuple(
            [
                await self.registry.resolve_active(
                    tenant_id=self.tenant_id, kind="skill", item_id=item_id,
                )
                for item_id in skill_ids
            ]
        )
        workflow = None
        if workflow_id:
            workflow = await self.registry.resolve_active(
                tenant_id=self.tenant_id, kind="workflow", item_id=workflow_id,
            )

        return agent, template, provider, tools, skills, workflow

    async def _resolve_pinned_items(self, record: Mapping[str, Any]) -> list[Any]:
        """Rebuild the original published references after a preparation crash.

        Never silently bind an idempotent retry to newer Agent/tool/Skill versions.
        """
        references = record.get("registry_snapshot")
        if not isinstance(references, (list, tuple)) or not references:
            raise WorkbenchTaskError("queued task is missing its persisted registry version references")
        items = []
        for reference in references:
            if not isinstance(reference, Mapping):
                raise WorkbenchTaskError("persisted registry reference is invalid")
            kind = reference.get("kind")
            item_id = reference.get("item_id")
            version = reference.get("version")
            if (
                not isinstance(kind, str) or not kind
                or not isinstance(item_id, str) or not item_id
                or isinstance(version, bool) or not isinstance(version, int) or version < 1
            ):
                raise WorkbenchTaskError("persisted registry reference is incomplete")
            item = await self.repository.get(self.tenant_id, kind, item_id, version)
            if item is None or item.status != "published":
                raise WorkbenchTaskError(
                    f"pinned registry version is unavailable: {kind}:{item_id}@{version}"
                )
            active = await self.registry.resolve_active(
                tenant_id=self.tenant_id, kind=kind, item_id=item_id,
            )
            if active.version != version:
                raise WorkbenchTaskError(
                    f"active registry version changed for pinned task dependency: {kind}:{item_id}"
                )
            items.append(item)
        return items

    async def _ensure_task_ready(
        self, *, tenant_id: str, run_id: str, agent_id: str,
        preparation_owner: str, existing_digest: str | None = None,
        pinned_items: Sequence[Any] | None = None,
    ) -> None:
        if existing_digest:
            snapshot = await self.snapshots.get(
                tenant_id=tenant_id, run_id=run_id,
            )
            if snapshot.digest != existing_digest:
                raise WorkbenchTaskError("stored Run registry snapshot digest does not match")
        else:
            if pinned_items is None:
                agent, template, provider, tools, skills, workflow = (
                    await self._runtime_references(agent_id)
                )
                resolved_items = [agent, template, provider, *tools, *skills]
                if workflow is not None:
                    resolved_items.append(workflow)
            else:
                resolved_items = list(pinned_items)
            references = [
                (item.kind, item.item_id, item.version) for item in resolved_items
            ]
            snapshot = await self.snapshots.pin_versions(
                tenant_id=tenant_id, run_id=run_id, references=references,
            )
        ready = await self.run_store.mark_task_ready(
            tenant_id, run_id, snapshot.digest, preparation_owner,
        )
        if not ready:
            current = await self.run_store.get_task_record(tenant_id, run_id)
            if current is not None and current.get("dispatch_ready"):
                return
            owns_lease = await self.run_store.owns_task_preparation(
                tenant_id, run_id, preparation_owner,
            )
            if owns_lease:
                raise RuntimeError("Run could not be marked ready after snapshot pinning")

    async def _prepare_or_wait(
        self, *, tenant_id: str, run_id: str, agent_id: str,
        preparation_owner: str, existing_digest: str | None = None,
        pinned_items: Sequence[Any] | None = None,
    ) -> None:
        try:
            await self._ensure_task_ready(
                tenant_id=tenant_id, run_id=run_id, agent_id=agent_id,
                preparation_owner=preparation_owner,
                existing_digest=existing_digest, pinned_items=pinned_items,
            )
        except Exception as exc:
            owns_lease = await self.run_store.owns_task_preparation(
                tenant_id, run_id, preparation_owner,
            )
            if not owns_lease:
                return
            current = await self.run_store.get(tenant_id, run_id)
            if current is not None and current.status == RunStatus.QUEUED:
                await self.lifecycle.finish(
                    tenant_id, run_id, RunStatus.FAILED,
                    error=f"registry snapshot preparation failed: {str(exc)[:400]}",
                )
            raise WorkbenchTaskError(
                "could not pin the Agent runtime registry snapshot"
            ) from exc

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
        run_id: str | None = None, priority: int = 100,
        parent_run_id: str | None = None, budget_lineage_key: str | None = None,
    ) -> Mapping[str, Any]:
        self._check_tenant(tenant_id)
        if not actor_id.strip():
            raise PermissionError("authenticated actor is required")
        if not isinstance(input, Mapping) or not input:
            raise WorkbenchTaskError("task input must be a non-empty object")
        if type(priority) is not int or not 0 <= priority <= 1000:
            raise WorkbenchTaskError("priority must be an integer between 0 and 1000")
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str)
            or not idempotency_key.strip() or len(idempotency_key) > 200
        ):
            raise WorkbenchTaskError("idempotency_key must contain 1 to 200 characters")

        if run_id is not None:
            try:
                run_id = str(UUID(run_id))
            except (ValueError, TypeError, AttributeError) as exc:
                raise WorkbenchTaskError("run_id must be a UUID") from exc

        if (parent_run_id is None) != (budget_lineage_key is None):
            raise WorkbenchTaskError("parent_run_id and budget_lineage_key must be supplied together")
        if parent_run_id is not None:
            try:
                parent_run_id = str(UUID(parent_run_id))
            except (ValueError, TypeError, AttributeError) as exc:
                raise WorkbenchTaskError("parent_run_id must be a UUID") from exc
            if not isinstance(budget_lineage_key, str) or not budget_lineage_key.strip():
                raise WorkbenchTaskError("budget_lineage_key must be non-empty")

        request_body = {
            "tenant_id": tenant_id, "actor_id": actor_id, "agent_id": agent_id,
            "project_id": project_id, "session_id": session_id, "input": dict(input),
        }
        # Keep default-priority request digests compatible with existing idempotency keys.
        if priority != 100:
            request_body["priority"] = priority
        # Deterministic child Run IDs are included only when explicitly supplied.
        if run_id is not None:
            request_body["requested_run_id"] = run_id
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
                if existing.get("status") == "queued" and not existing.get("dispatch_ready"):
                    owner = str(uuid4())
                    claimed = await self.run_store.claim_task_preparation(
                        tenant_id, str(existing["run_id"]), owner,
                    )
                    if claimed:
                        await self._prepare_or_wait(
                            tenant_id=tenant_id, run_id=str(existing["run_id"]),
                            agent_id=agent_id, preparation_owner=owner,
                            existing_digest=existing.get("registry_snapshot_digest"),
                            pinned_items=await self._resolve_pinned_items(existing),
                        )
                    existing = await self.run_store.get_task_record(
                        tenant_id, str(existing["task_id"]), actor_id=actor_id,
                    )
                return _task_view(existing)

        agent, template, provider, tools, skills, workflow = (
            await self._runtime_references(agent_id)
        )
        template_id = template.item_id
        template_version = template.version
        run_id = run_id or str(uuid4())
        preparation_owner = str(uuid4())
        create_run_args = dict(
            tenant_id=tenant_id, run_id=run_id, actor_id=actor_id,
            agent_id=agent_id, agent_version=agent.version,
            template_id=template_id, template_version=template_version,
            input=dict(input), preparation_owner=preparation_owner,
            priority=priority, project_id=project_id, session_id=session_id,
            workflow_id=workflow.item_id if workflow is not None else None,
            idempotency_key=idempotency_key, request_digest=digest,
            registry_snapshot=[
                {"kind": item.kind, "item_id": item.item_id, "version": item.version}
                for item in [agent, template, provider, *tools, *skills, *([workflow] if workflow is not None else [])]
            ],
        )
        if parent_run_id is not None:
            create_run_args["parent_run_id"] = parent_run_id
            create_run_args["budget_lineage_key"] = budget_lineage_key
        created = await self.run_store.create_task_run(**create_run_args)
        if not created:
            existing = await self.run_store.get_task_by_idempotency(
                tenant_id, actor_id, idempotency_key,
            )
            if existing is None:
                raise RuntimeError("idempotent task record disappeared")
            if existing.get("status") == "queued" and not existing.get("dispatch_ready"):
                owner = str(uuid4())
                claimed = await self.run_store.claim_task_preparation(
                    tenant_id, str(existing["run_id"]), owner,
                )
                if claimed:
                    await self._prepare_or_wait(
                        tenant_id=tenant_id, run_id=str(existing["run_id"]),
                        agent_id=agent_id, preparation_owner=owner,
                        existing_digest=existing.get("registry_snapshot_digest"),
                        pinned_items=await self._resolve_pinned_items(existing),
                    )
                existing = await self.run_store.get_task_record(
                    tenant_id, str(existing["task_id"]), actor_id=actor_id,
                )
            return _task_view(existing)

        pinned_items = [agent, template, provider, *tools, *skills]
        if workflow is not None:
            pinned_items.append(workflow)
        await self._prepare_or_wait(
            tenant_id=tenant_id, run_id=run_id, agent_id=agent_id,
            preparation_owner=preparation_owner, pinned_items=pinned_items,
        )

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

    async def retry_task(
        self, *, tenant_id: str, task_id: str, actor_id: str,
        idempotency_key: str,
    ) -> Mapping[str, Any] | None:
        self._check_tenant(tenant_id)
        if not actor_id.strip():
            raise PermissionError("authenticated actor is required")
        if (
            not isinstance(idempotency_key, str)
            or not idempotency_key.strip() or len(idempotency_key) > 150
        ):
            raise WorkbenchTaskError("idempotency_key must contain 1 to 150 characters")
        source = await self.run_store.get_task_record(
            tenant_id, task_id, actor_id=actor_id,
        )
        if source is None:
            return None
        if source["status"] not in {"failed", "timed_out"}:
            raise WorkbenchTaskError("only failed or timed_out tasks can be retried")
        source_run_id = str(source["run_id"])
        retry_key = f"retry:{source_run_id}:{idempotency_key}"
        digest_body = {
            "tenant_id": tenant_id, "actor_id": actor_id,
            "retry_of_run_id": source_run_id,
        }
        digest = hashlib.sha256(json.dumps(
            digest_body, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        new_run_id = str(uuid4())
        retried_run_id = await self.run_store.create_retry_task_run(
            tenant_id=tenant_id, task_id=task_id, actor_id=actor_id,
            run_id=new_run_id, idempotency_key=retry_key, request_digest=digest,
        )
        retried = await self.run_store.get_task_record(
            tenant_id, retried_run_id, actor_id=actor_id,
        )
        return _task_view(retried) if retried is not None else None

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
        run_context_resolver: Callable[[str], Any] | None = None,
    ) -> None:
        self.snapshots = snapshots
        self.lifecycle_resolver = lifecycle_resolver
        self.runtime_provider_resolver = runtime_provider_resolver
        self.graph_factory = graph_factory
        self.run_context_resolver = run_context_resolver\n        self.event_broker_resolver = event_broker_resolver

    async def _resolve(self, callback: Callable[..., Any], *args: Any) -> Any:
        value = callback(*args)
        return await value if inspect.isawaitable(value) else value

    async def __call__(
        self, record: Mapping[str, Any], worker_id: str,
    ) -> Any:
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
        if matches[0].get("version") != record.get("agent_version"):
            raise ValueError("Run snapshot Agent version differs from the persisted task version")
        template_matches = [
            item for item in snapshot.entries
            if item.get("kind") == "model_template"
            and item.get("item_id") == template_id
        ]
        if len(template_matches) != 1:
            raise ValueError("Run snapshot does not contain the selected model template")
        if template_matches[0].get("version") != record.get("template_version"):
            raise ValueError("Run snapshot model template version differs from the persisted task version")
        agent_definition = dict(matches[0]["definition"])
        if agent_definition.get("model_template_id") != template_id:
            raise ValueError("Run-pinned Agent references a different model template")
        tool_ids = RegistryBackedTaskCenterService._string_list(
            agent_definition.get("tool_ids", []), "tool_ids",
        )
        skill_ids = RegistryBackedTaskCenterService._string_list(
            agent_definition.get("skill_ids", []), "skill_ids",
        )
        model_definition = template_matches[0].get("definition")
        if not isinstance(model_definition, Mapping):
            raise ValueError("Run snapshot model template definition is invalid")
        provider_id = model_definition.get("provider_id")
        provider_version = model_definition.get("provider_version")
        providers = [
            item for item in snapshot.entries
            if item.get("kind") == "model_provider"
        ]
        if (
            len(providers) != 1
            or providers[0].get("item_id") != provider_id
            or providers[0].get("version") != provider_version
        ):
            raise ValueError("Run snapshot model provider does not match the pinned template")
        snapshot_tools = {
            item.get("item_id") for item in snapshot.entries
            if item.get("kind") == "tool"
        }
        snapshot_skills = {
            item.get("item_id") for item in snapshot.entries
            if item.get("kind") == "skill"
        }
        if snapshot_tools != set(tool_ids) or snapshot_skills != set(skill_ids):
            raise ValueError("Run snapshot tool/Skill set differs from the pinned Agent definition")
        workflow_id = agent_definition.get("workflow_id")
        snapshot_workflows = {
            item.get("item_id") for item in snapshot.entries
            if item.get("kind") == "workflow"
        }
        expected_workflows = {workflow_id} if workflow_id else set()
        if snapshot_workflows != expected_workflows:
            raise ValueError("Run snapshot workflow differs from the pinned Agent definition")
        lifecycle = await self._resolve(self.lifecycle_resolver, tenant_id)
        runtime_provider = await self._resolve(
            self.runtime_provider_resolver, tenant_id,
        )
        run_context_service = None
        if self.run_context_resolver is not None:
            run_context_service = await self._resolve(
                self.run_context_resolver, tenant_id,
            )
        if lifecycle.store is None or runtime_provider is None:
            raise RuntimeError("durable Run lifecycle and registry runtime provider are required")
        graph = self.graph_factory(
            agent_definition=agent_definition,
            snapshot=snapshot,
            task_record=record,
            runtime_provider=runtime_provider,
        )
        if inspect.isawaitable(graph):
            graph = await graph

        def build_executor(compiled_graph: Any) -> LangGraphRunExecutor:
            return LangGraphRunExecutor(
                graph=compiled_graph, lifecycle=lifecycle, worker_id=worker_id,
                registry_snapshot_service=self.snapshots,
                registry_runtime_provider=runtime_provider,
                model_template_id=template_id,
                required_tools=tuple(tool_ids),
                run_context_service=run_context_service,
            )

        if callable(getattr(graph, "__aenter__", None)) and callable(
            getattr(graph, "__aexit__", None)
        ):
            @asynccontextmanager
            async def scoped_executor() -> Any:
                async with graph as compiled_graph:
                    yield build_executor(compiled_graph)
            return scoped_executor()
        return build_executor(graph)


class RunExecutionWorker:
    """Lease-claiming worker for queued, interrupted and remotely cancelled Runs."""

    def __init__(
        self, *, run_store: Any, executor_resolver: Callable[..., Any],
        lifecycle_resolver: Callable[[str], Any], worker_id: str,
        poll_interval: float = 1.0, batch_size: int = 10, lease_seconds: int = 60,
        concurrency_limiter: Any,
        worker_pool_scope_id: str,
        queue_scope_resolver: Callable[[Mapping[str, Any]], str],
    ) -> None:
        if not worker_id.strip() or poll_interval <= 0:
            raise ValueError("worker_id and positive poll_interval are required")
        if not 1 <= batch_size <= 200 or lease_seconds < 5:
            raise ValueError("invalid worker batch or lease configuration")
        if not callable(getattr(concurrency_limiter, "slot", None)):
            raise ValueError("a shared concurrency limiter with slot() is required")
        if not isinstance(worker_pool_scope_id, str) or not worker_pool_scope_id.strip():
            raise ValueError("worker_pool_scope_id is required")
        if not callable(queue_scope_resolver):
            raise ValueError("queue_scope_resolver is required for tenant queue quota enforcement")
        self.run_store = run_store
        self.executor_resolver = executor_resolver
        self.lifecycle_resolver = lifecycle_resolver
        self.worker_id = worker_id
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self.concurrency_limiter = concurrency_limiter
        self.worker_pool_scope_id = worker_pool_scope_id
        self.queue_scope_resolver = queue_scope_resolver
        self.lease_seconds = lease_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[Any] | None = None

    async def _resolve(self, callback: Callable[..., Any], *args: Any) -> Any:
        value = callback(*args)
        return await value if inspect.isawaitable(value) else value

    async def _execute_claimed(
        self, snapshot: Any, resolved: Any, record: Mapping[str, Any],
    ) -> str:
        tenant_id, run_id = snapshot.tenant_id, snapshot.run_id
        session_id = record.get("session_id")
        actor_id = record.get("created_by") or record.get("actor_id")
        session_context = None
        if session_id is not None:
            session_id = str(session_id)
            if not session_id.strip():
                raise WorkbenchTaskError("persisted session_id is invalid")
            if not isinstance(actor_id, str) or not actor_id.strip():
                raise WorkbenchTaskError("session-backed Run is missing its actor identity")
            session_context = {"session_id": session_id, "actor_id": actor_id}
        if not isinstance(resolved, LangGraphRunExecutor):
            raise TypeError("executor_resolver must return LangGraphRunExecutor or an async context manager")
        if resolved.worker_id != self.worker_id:
            raise ValueError("resolved executor must use the claiming worker_id")
        if resolved.lifecycle.store is not self.run_store:
            raise ValueError("resolved executor must share the worker RunStateStore")
        if snapshot.status == RunStatus.QUEUED:
            result = await resolved.execute(
                tenant_id=tenant_id, run_id=run_id, input=record.get("input") or {},
                session_context=session_context,
            )
        else:
            result = await resolved.resume_claimed(
                snapshot=snapshot, input=record.get("input") or {},
                session_context=session_context,
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

    async def _heartbeat_until_done(self, tenant_id: str, run_id: str) -> None:
        heartbeat = getattr(self.run_store, "heartbeat", None)
        if not callable(heartbeat):
            raise RuntimeError("RunStateStore must implement worker lease heartbeat")
        interval = max(1.0, self.lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            owned = await heartbeat(
                tenant_id, run_id, self.worker_id,
                lease_seconds=self.lease_seconds,
            )
            if not owned:
                raise WorkerLeaseLost(
                    f"worker lease lost while executing Run {run_id}"
                )

    async def _with_heartbeat(self, snapshot: Any, operation: Any) -> str:
        """Keep the lease alive across the entire claimed operation, including slot waits."""
        execution = asyncio.create_task(operation)
        heartbeat = asyncio.create_task(
            self._heartbeat_until_done(snapshot.tenant_id, snapshot.run_id)
        )
        try:
            done, _pending = await asyncio.wait(
                {execution, heartbeat},
                return_when=asyncio.FIRST_COMPLETED,
            )
            # A fast execution may finish and release its lease in the same
            # event-loop turn in which the heartbeat observes the terminal state.
            # Prefer the completed execution result when both tasks are done.
            if execution in done:
                return await execution
            if heartbeat in done:
                error = heartbeat.exception()
                if error is not None:
                    execution.cancel()
                    await asyncio.gather(execution, return_exceptions=True)
                    raise error
            return await execution
        finally:
            for task in (heartbeat, execution):
                if not task.done():
                    task.cancel()
            await asyncio.gather(heartbeat, execution, return_exceptions=True)

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
                async def execute_claimed() -> str:
                    async with AsyncExitStack() as slots:
                        if self.concurrency_limiter is not None:
                            queue_id = self.queue_scope_resolver(record)
                            if not isinstance(queue_id, str) or not queue_id.strip():
                                raise ValueError("queue scope resolver must return a non-empty string")
                            await slots.enter_async_context(self.concurrency_limiter.slot(
                                tenant_id=tenant_id, run_id=run_id,
                                scope_type="queue", scope_id=queue_id,
                            ))
                            await slots.enter_async_context(self.concurrency_limiter.slot(
                                tenant_id=tenant_id, run_id=run_id,
                                scope_type="worker_pool", scope_id=self.worker_pool_scope_id,
                            ))
                        resolved = await self._resolve(
                            self.executor_resolver, record, self.worker_id,
                        )
                        if callable(getattr(resolved, "__aenter__", None)) and callable(
                            getattr(resolved, "__aexit__", None)
                        ):
                            async with resolved as executor:
                                return await self._execute_claimed(snapshot, executor, record)
                        return await self._execute_claimed(snapshot, resolved, record)

                return await self._with_heartbeat(snapshot, execute_claimed())
            except asyncio.CancelledError:
                raise
            except BudgetExceeded:
                # Leave the Run queued; the next poll retries after a shared slot frees.
                return "skipped"
            except WorkerLeaseLost:
                current = await self.run_store.get(tenant_id, run_id)
                return "cancelled" if current and current.status == RunStatus.CANCELLED else "skipped"
            except Exception as exc:
                lifecycle = await self._resolve(self.lifecycle_resolver, tenant_id)
                current = await self.run_store.get(tenant_id, run_id)
                if current and current.status == RunStatus.CANCELLING:
                    await lifecycle.finish(tenant_id, run_id, RunStatus.CANCELLED)
                elif current and current.status not in {
                    RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.TIMED_OUT,
                }:
                    record_failure = getattr(
                        self.run_store, "record_workbench_failure", None,
                    )
                    if callable(record_failure):
                        await record_failure(
                            tenant_id, run_id, self.worker_id,
                            error=f"workbench worker failed: {str(exc)[:400]}",
                        )
                    else:
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
