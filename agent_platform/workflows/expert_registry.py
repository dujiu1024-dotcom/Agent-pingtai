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
        if not self.expert_id.strip() or not self.name.strip():
            raise ValueError("expert_id and name must not be empty")
        if any(not item.strip() for item in self.capabilities):
            raise ValueError("expert capabilities must not be empty")


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
        capabilities = frozenset(str(item) for item in raw_capabilities)
        if any(not item.strip() for item in capabilities):
            raise ValueError("published Agent capabilities must not be empty")
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
