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
        if not isinstance(self.decision, MigrationDecision):
            raise ValueError("decision must be a MigrationDecision")
        if self.decision in (MigrationDecision.REUSE, MigrationDecision.ADAPT,
                             MigrationDecision.REPLACE) and not self.target:
            raise ValueError("active migration decisions require a target")
        if self.target is not None and (not isinstance(self.target, str) or not self.target.strip()):
            raise ValueError("target must be a non-empty string when provided")
        if not isinstance(self.notes, str):
            raise ValueError("notes must be a string")

# Targets identify current repository capability candidates, not proof of data parity.
# Legacy source paths are historical labels; their original contents are not present here.
DEFAULT_LEGACY_ASSETS = (
    LegacyAsset("agency", "agency.py", MigrationDecision.ADAPT, "platform",
                "agent_platform/runtime/harness_factory.py",
                "Candidate mapping only; compare agent orchestration, tool policy and lifecycle semantics."),
    LegacyAsset("database", "db.py", MigrationDecision.ADAPT, "data",
                "agent_platform/storage/postgres/",
                "Candidate persistence target; reconcile legacy tables, constraints, transactions and retention."),
    LegacyAsset("memory", "memory.py", MigrationDecision.ADAPT, "memory",
                "agent_platform/memory/",
                "Candidate memory service/repository; validate extraction, retrieval, expiry and tenant isolation."),
    LegacyAsset("vectors", "vectors.py", MigrationDecision.ADAPT, "knowledge",
                "agent_platform/knowledge/rag.py",
                "Candidate vector retrieval path; validate embedding model, dimensions, chunk IDs and ranking."),
    LegacyAsset("conversation", "conversation.py", MigrationDecision.ADAPT, "runtime",
                "agent_platform/api/sessions.py; agent_platform/runtime/",
                "Candidate session and execution path; validate history ordering, streaming and tool-call semantics."),
    LegacyAsset("gui", "gui.py", MigrationDecision.REPLACE, "product",
                "agent_platform/api/workbench.py",
                "Candidate API workbench replacement; original GUI behavior and client parity remain unverified."),
    LegacyAsset("feishu", "feishu integration", MigrationDecision.ADAPT, "integrations",
                "agent_platform/api/connectors.py; agent_platform/knowledge/connectors.py",
                "Generic connector capability exists; Feishu-specific auth, events, sync and rate limits need adapter validation."),
)

def validate_inventory(assets: Iterable[LegacyAsset]) -> tuple[LegacyAsset, ...]:
    records = tuple(assets)
    if any(not isinstance(item, LegacyAsset) for item in records):
        raise ValueError("inventory entries must be LegacyAsset instances")
    names = [item.name for item in records]
    paths = [item.path for item in records]
    if len(names) != len(set(names)) or len(paths) != len(set(paths)):
        raise ValueError("legacy inventory names and paths must be unique")
    return tuple(sorted(records, key=lambda item: item.name))

def inventory_report(assets: Iterable[LegacyAsset] = DEFAULT_LEGACY_ASSETS) -> dict[str, object]:
    """Return stable mapping candidates without claiming semantic migration completion."""
    records = validate_inventory(assets)
    counts = {decision.value: 0 for decision in MigrationDecision}
    for item in records:
        counts[item.decision.value] += 1
    return {
        "total": len(records),
        "assessed": len(records) - counts[MigrationDecision.UNASSESSED.value],
        "unassessed": counts[MigrationDecision.UNASSESSED.value],
        "by_decision": counts,
        "mapping_candidates": sum(item.target is not None for item in records),
        "assets": [{"name": item.name, "path": item.path, "decision": item.decision.value,
                    "owner": item.owner, "target": item.target, "notes": item.notes}
                   for item in records],
    }
