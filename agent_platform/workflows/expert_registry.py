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
