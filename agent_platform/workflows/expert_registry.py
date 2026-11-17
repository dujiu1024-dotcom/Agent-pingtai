"""Expert capability catalog contracts for supervised multi-expert workflows."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ExpertProfile:
    expert_id: str
    name: str
    description: str
    capabilities: frozenset[str]
    enabled: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.expert_id, str) or not self.expert_id.strip():
            raise ValueError("expert_id must be a non-empty string")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("expert name must be a non-empty string")
        if not isinstance(self.description, str):
            raise ValueError("expert description must be a string")
        if not isinstance(self.enabled, bool):
            raise ValueError("expert enabled flag must be boolean")
        if not isinstance(self.capabilities, (set, frozenset, list, tuple)):
            raise ValueError("expert capabilities must be a collection of strings")
        normalized = frozenset(self.capabilities)
        if any(not isinstance(item, str) or not item.strip() for item in normalized):
            raise ValueError("expert capabilities must contain non-empty strings")
        object.__setattr__(self, "capabilities", normalized)


class ExpertRegistry(Protocol):
    async def get(self, expert_id: str) -> ExpertProfile | None:
        """Return the tenant-authorized expert profile, if registered."""
        ...


class InMemoryExpertRegistry:
    """Small test adapter; production resolves profiles from the control plane."""

    def __init__(self, profiles: tuple[ExpertProfile, ...] = ()) -> None:
        self._profiles = {profile.expert_id: profile for profile in profiles}

    async def get(self, expert_id: str) -> ExpertProfile | None:
        return self._profiles.get(expert_id)

    def register(self, profile: ExpertProfile) -> None:
        self._profiles[profile.expert_id] = profile


class ControlPlaneExpertRegistry:
    """Resolve active Agent definitions from the tenant-scoped control-plane registry.

    Only published active Agent versions are eligible. Runtime objects and
    credentials are never read from registry metadata; the Harness remains
    responsible for resolving templates and Gateway capabilities.
    """

    def __init__(self, *, registry_service, tenant_id: str) -> None:
        if not tenant_id.strip():
            raise ValueError("tenant_id must not be empty")
        self.registry_service = registry_service
        self.tenant_id = tenant_id

    async def get(self, expert_id: str) -> ExpertProfile | None:
        from agent_platform.control_plane.registry import RegistryNotFound

        if not expert_id.strip():
            return None
        try:
            version = await self.registry_service.resolve_active(
                tenant_id=self.tenant_id, kind="agent", item_id=expert_id,
            )
        except RegistryNotFound:
            return None
        definition = version.definition
        raw_capabilities = definition.get("capabilities", ())
        if not isinstance(raw_capabilities, (list, tuple, set, frozenset)):
            raise ValueError("published Agent capabilities must be an array")
        if any(not isinstance(item, str) or not item.strip() for item in raw_capabilities):
            raise ValueError("published Agent capabilities must contain non-empty strings")
        capabilities = frozenset(raw_capabilities)
        enabled = definition.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("published Agent enabled flag must be boolean")
        return ExpertProfile(
            expert_id=version.item_id,
            name=str(definition.get("name") or version.item_id),
            description=str(definition.get("description") or ""),
            capabilities=capabilities,
            enabled=enabled and version.status == "published",
        )


@dataclass(frozen=True)
class ExpertAssignment:
    """Validated, serializable dispatch contract for one expert in a task."""

    assignment_id: str
    task_id: str
    expert_id: str
    focus: str
    required_capabilities: frozenset[str]
    declared_capabilities: frozenset[str]

    def __post_init__(self) -> None:
        for field_name in ("assignment_id", "task_id", "expert_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be empty")
        if not isinstance(self.focus, str):
            raise ValueError("assignment focus must be a string")
        for field_name in ("required_capabilities", "declared_capabilities"):
            values = getattr(self, field_name)
            if any(not isinstance(item, str) or not item.strip() for item in values):
                raise ValueError(f"{field_name} must contain non-empty strings")
        if not self.required_capabilities.issubset(self.declared_capabilities):
            raise ValueError("required capabilities exceed the expert declaration")

    def to_dict(self) -> dict[str, object]:
        return {
            "assignment_id": self.assignment_id,
            "task_id": self.task_id,
            "expert_id": self.expert_id,
            "focus": self.focus,
            "required_capabilities": sorted(self.required_capabilities),
            "declared_capabilities": sorted(self.declared_capabilities),
        }
