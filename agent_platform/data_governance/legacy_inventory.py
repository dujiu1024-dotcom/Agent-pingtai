"""Machine-readable inventory for legacy platform assets and migration decisions."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class MigrationDecision(str, Enum):
    REUSE = "reuse"
    ADAPT = "adapt"
    REPLACE = "replace"
    RETIRE = "retire"
    UNASSESSED = "unassessed"


@dataclass(frozen=True)
class LegacyAsset:
    name: str
    path: str
    decision: MigrationDecision
    owner: str
    target: str | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        for field_name in ("name", "path", "owner"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
        if self.decision in (MigrationDecision.REUSE, MigrationDecision.ADAPT,
                             MigrationDecision.REPLACE) and not self.target:
            raise ValueError("active migration decisions require a target")


DEFAULT_LEGACY_ASSETS = (
    LegacyAsset("agency", "agency.py", MigrationDecision.UNASSESSED, "platform"),
    LegacyAsset("database", "db.py", MigrationDecision.UNASSESSED, "data"),
    LegacyAsset("memory", "memory.py", MigrationDecision.UNASSESSED, "memory"),
    LegacyAsset("vectors", "vectors.py", MigrationDecision.UNASSESSED, "knowledge"),
    LegacyAsset("conversation", "conversation.py", MigrationDecision.UNASSESSED, "runtime"),
    LegacyAsset("gui", "gui.py", MigrationDecision.UNASSESSED, "product"),
    LegacyAsset("feishu", "feishu integration", MigrationDecision.UNASSESSED, "integrations"),
)


def validate_inventory(assets: Iterable[LegacyAsset]) -> tuple[LegacyAsset, ...]:
    records = tuple(assets)
    names = [item.name for item in records]
    paths = [item.path for item in records]
    if len(names) != len(set(names)) or len(paths) != len(set(paths)):
        raise ValueError("legacy inventory names and paths must be unique")
    return tuple(sorted(records, key=lambda item: item.name))
