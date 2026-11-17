"""Compose a Run's registry-pinned model, tools and reviewed Skills.

The returned model client resolves secrets only when a model call is made. No
credential is copied into LangGraph config, checkpoint state, or Run snapshots.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from agent_platform.control_plane.registry import RegistryVersion
from agent_platform.control_plane.skill_dependency_review import (
    SkillDependencyReview,
    SkillDependencyReviewer,
)
from agent_platform.models.runtime_router import (
    OpenAICompatibleModelRouter,
    RoutedModelResponse,
)
from agent_platform.runtime.registry_snapshot import RunRegistrySnapshotService
from agent_platform.tools.gateway import ToolDefinition
from agent_platform.tools.registry_catalog import build_tool_definitions


class RegistryRuntimeError(RuntimeError):
    pass


class RunScopedModelClient:
    """Resolve the immutable snapshot for every call; never cache plaintext keys."""

    def __init__(
        self, *, tenant_id: str, run_id: str, template_id: str,
        snapshots: RunRegistrySnapshotService,
        router: OpenAICompatibleModelRouter,
    ) -> None:
        self.tenant_id = tenant_id
        self.run_id = run_id
        self.template_id = template_id
        self.snapshots = snapshots
        self.router = router

    async def complete(
        self, *, messages: Sequence[Mapping[str, Any]], request_id: str,
        required_capabilities: Sequence[str] = (), data_level: str = "internal",
        fallback_template_ids: Sequence[str] = (),
        request_parameters: Mapping[str, Any] | None = None,
    ) -> RoutedModelResponse:
        snapshot = await self.snapshots.get(
            tenant_id=self.tenant_id, run_id=self.run_id,
        )
        from agent_platform.streaming.context import (
            current_run_event_callback, current_token_callback,
        )
        token_callback = current_token_callback()
        event_callback = current_run_event_callback()
        if event_callback is not None:
            await event_callback("LLM_REQUEST", model_template=self.template_id)
        try:
            result = await self.router.complete(
                snapshot=snapshot, tenant_id=self.tenant_id, run_id=self.run_id,
                template_id=self.template_id, messages=messages, request_id=request_id,
                required_capabilities=required_capabilities, data_level=data_level,
                fallback_template_ids=fallback_template_ids,
                request_parameters=request_parameters,
                on_token=token_callback,
            )
        except Exception as exc:
            if event_callback is not None:
                await event_callback("ERROR", step="model", message=type(exc).__name__)
            raise
        if event_callback is not None:
            await event_callback(
                "LLM_RESPONSE", model_id=result.model_id,
                usage=dict(result.usage),
            )
        return result


@dataclass/(frozen=True)
class RunRegistryRuntime:
    tenant_id: str
    run_id: str
    snapshot_digest: str
    model: RunScopedModelClient
    tools: tuple[ToolDefinition, ...]
    skill_reviews: tuple[SkillDependencyReview, ...]


class RegistryRuntimeAssembler:
    """Build the runtime surface from one immutable tenant/run registry snapshot."""

    def __init__(
        self, *, snapshots: RunRegistrySnapshotService,
        model_router: OpenAICompatibleModelRouter,
        skill_reviewer: SkillDependencyReviewer,
        trusted_tool_handlers: Mapping[str, ToolDefinition],
    ) -> None:
        required_budget_components = {
            "token_estimator": "estimate",
            "quota_service": "reserve_run",
            "quota_cost_estimator": "estimate_micros",
            "audit_sink": "record",
        }
        missing_budget_components = [
            f"{name}.{method}"
            for name, method in required_budget_components.items()
            if not callable(getattr(getattr(model_router, name, None), method, None))
        ]
        if missing_budget_components:
            raise RegistryRuntimeError(
                "production Run model router must configure budget enforcement and model audit: "
                + ", ".join(missing_budget_components)
            )
        self.snapshots = snapshots
        self.model_router = model_router
        self.skill_reviewer = skill_reviewer
        self.trusted_tool_handlers = dict(trusted_tool_handlers)

    @staticmethod
    def _registry_version(
        snapshot: Any, entry: Mapping[str, Any],
    ) -> RegistryVersion:
        kind = entry.get("kind")
        item_id = entry.get("item_id")
        version = entry.get("version")
        definition = entry.get("definition")
        if (
            not isinstance(kind, str) or not isinstance(item_id, str)
            or not isinstance(version, int) or version < 1
            or not isinstance(definition, Mapping)
        ):
            raise RegistryRuntimeError("Run snapshot contains an invalid registry entry")
        return RegistryVersion(
            tenant_id=snapshot.tenant_id, kind=kind, item_id=item_id,
            version=version, definition=dict(definition), status="published",
            created_by="run-snapshot", created_at=datetime.now(timezone.utc),
        )

    async def assemble(
        self, *, tenant_id: str, run_id: str, model_template_id: str,
        required_tools: Sequence[str] = (),
    ) -> RunRegistryRuntime:
        snapshot = await self.snapshots.get(tenant_id=tenant_id, run_id=run_id)
        if snapshot.tenant_id != tenant_id or snapshot.run_id != run_id:
            raise RegistryRuntimeError("Run registry snapshot scope mismatch")
        entries = tuple(snapshot.entries)
        model_matches = [
            entry for entry in entries
            if entry.get("kind") == "model_template"
            and entry.get("item_id") == model_template_id
        ]
        if len(model_matches) != 1:
            raise RegistryRuntimeError("Run snapshot must pin exactly one selected model template")

        tool_versions = tuple(
            self._registry_version(snapshot, entry)
            for entry in entries if entry.get("kind") == "tool"
        )
        tools = build_tool_definitions(
            published_tools=tool_versions,
            trusted_handlers=self.trusted_tool_handlers,
        )
        tool_names = {tool.name for tool in tools}
        missing_tools = set(required_tools) - tool_names
        if missing_tools:
            raise RegistryRuntimeError(
                "Run snapshot is missing required registered tools: "
                + ", ".join(sorted(missing_tools))
            )

        reviews: list[SkillDependencyReview] = []
        for entry in entries:
            if entry.get("kind") != "skill":
                continue
            skill = self._registry_version(snapshot, entry)
            try:
                reviews.append(await self.skill_reviewer.require_approved(
                    tenant_id=tenant_id, item=skill,
                ))
            except Exception as exc:
                raise RegistryRuntimeError(
                    f"Skill {skill.item_id} is not approved for this Run"
                ) from exc

        return RunRegistryRuntime(
            tenant_id=tenant_id, run_id=run_id, snapshot_digest=snapshot.digest,
            model=RunScopedModelClient(
                tenant_id=tenant_id, run_id=run_id,
                template_id=model_template_id, snapshots=self.snapshots,
                router=self.model_router,
            ),
            tools=tools, skill_reviews=tuple(reviews),
        )


class RunRuntimeStore:
    """Worker-local, tenant/run-keyed runtime cache; rebuilt from durable snapshots."""

    def __init__(self) -> None:
        import asyncio
        self._rows: dict[tuple[str, str], RunRegistryRuntime] = {}
        self._lock = asyncio.Lock()

    async def save(self, runtime: RunRegistryRuntime) -> None:
        async with self._lock:
            self._rows[(runtime.tenant_id, runtime.run_id)] = runtime

    async def get(self, tenant_id: str, run_id: str) -> RunRegistryRuntime:
        async with self._lock:
            runtime = self._rows.get((tenant_id, run_id))
        if runtime is None:
            raise RegistryRuntimeError("Run runtime has not been prepared by its worker")
        return runtime

    async def delete(self, tenant_id: str, run_id: str) -> None:
        async with self._lock:
            self._rows.pop((tenant_id, run_id), None)


class RegistryRuntimeProvider:
    """Prepare/release a Run runtime around execution and rebuild it after recovery."""

    def __init__(
        self, *, assembler: RegistryRuntimeAssembler,
        store: RunRuntimeStore | None = None,
    ) -> None:
        self.assembler = assembler
        self.store = store or RunRuntimeStore()

    async def prepare(
        self, *, tenant_id: str, run_id: str, model_template_id: str,
        required_tools: Sequence[str] = (),
    ) -> RunRegistryRuntime:
        runtime = await self.assembler.assemble(
            tenant_id=tenant_id, run_id=run_id,
            model_template_id=model_template_id, required_tools=required_tools,
        )
        await self.store.save(runtime)
        return runtime

    async def get(self, tenant_id: str, run_id: str) -> RunRegistryRuntime:
        return await self.store.get(tenant_id, run_id)

    async def release(self, tenant_id: str, run_id: str) -> None:
        await self.store.delete(tenant_id, run_id)
