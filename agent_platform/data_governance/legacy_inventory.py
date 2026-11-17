"""Machine-readable inventory for legacy platform assets and migration decisions."""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable


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
                "Candidate mapping only; persistence target; reconcile legacy tables, constraints, transactions and retention."),
    LegacyAsset("memory", "memory.py", MigrationDecision.ADAPT, "memory",
                "agent_platform/memory/",
                "Candidate mapping only; memory service/repository; validate extraction, retrieval, expiry and tenant isolation."),
    LegacyAsset("vectors", "vectors.py", MigrationDecision.ADAPT, "knowledge",
                "agent_platform/knowledge/rag.py",
                "Candidate mapping only; vector retrieval path; validate embedding model, dimensions, chunk IDs and ranking."),
    LegacyAsset("conversation", "conversation.py", MigrationDecision.ADAPT, "runtime",
                "agent_platform/api/sessions.py; agent_platform/runtime/",
                "Candidate mapping only; session and execution path; validate history ordering, streaming and tool-call semantics."),
    LegacyAsset("gui", "gui.py", MigrationDecision.REPLACE, "product",
                "agent_platform/api/workbench.py",
                "Candidate mapping only; API workbench replacement; original GUI behavior and client parity remain unverified."),
    LegacyAsset("feishu", "feishu integration", MigrationDecision.ADAPT, "integrations",
                "agent_platform/api/connectors.py; agent_platform/knowledge/connectors.py",
                "Candidate mapping only; generic connector capability exists; Feishu-specific auth, events, sync and rate limits need adapter validation."),
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


_SENSITIVE = re.compile(r"password|passwd|secret|token|api[_-]?key|credential|private[_-]?key", re.I)
_IDENTITY = re.compile(r"(^|_)(user|users|owner|actor|subject|open_id|identity|member)(_|$)", re.I)
_BUSINESS = re.compile(r"tenant|project|customer|client|task|order|contract|invoice|company", re.I)
_EXCLUDED = {".git", ".venv", "venv", "__pycache__", "node_modules", ".mypy_cache", ".ruff_cache"}
_LEGACY_ROOTS = ("agency.py", "db.py", "memory.py", "vectors.py", "conversation.py", "intent.py",
                 "console.py", "console.html", "listener.py", "agency", "agents", "clients",
                 "company", "templates")
_BUSINESS_MAPPINGS = (
    {"source": "agency/state/agency.db:projects", "target": "agent_platform tenancy/project domain",
     "status": "candidate_unverified", "evidence_required": "project identifiers, ownership, lifecycle and tenant boundary"},
    {"source": "agency/state/agency.db:tasks;events;reminders;agent_runs;dead_letter", "target": "agent_platform task_center/runs/events",
     "status": "candidate_unverified", "evidence_required": "task state machine, dependencies, event idempotency and run linkage"},
    {"source": "agency/state/agency.db:users;conversations", "target": "agent_platform identity/sessions",
     "status": "candidate_unverified", "evidence_required": "open_id provider binding, account claims, roles and conversation ownership"},
    {"source": "agency/state/agency.db:clients;quotes;contracts;invoices;payments;costs", "target": "business/finance domain (not yet proven in platform)",
     "status": "gap_requires_domain_design", "evidence_required": "customer identity, contractual lineage, financial precision and retention"},
    {"source": "agency/state/agency.db:facts;docs;embeddings;entities;relations", "target": "agent_platform knowledge/RAG",
     "status": "candidate_unverified", "evidence_required": "source lineage, classification, chunk identity, vector model/dimensions and deletion propagation"},
    {"source": "agency/state/agency.db:feishu_tasks;dependencies;deliveries;content_plan;subscriptions;config_kv", "target": "agent_platform connectors/integrations and product-specific domains",
     "status": "candidate_unverified", "evidence_required": "remote IDs, sync conflict policy, credential rotation and business ownership"},
)


def _risk(path: str, category: str, size: int = 0,
          sensitive_columns: list[str] | None = None) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if _SENSITIVE.search(path) or sensitive_columns:
        reasons.append("credential_or_secret_material")
    if _IDENTITY.search(path) or category == "identity":
        reasons.append("identity_or_personal_data")
    if _BUSINESS.search(path) or category == "business_data":
        reasons.append("business_confidentiality")
    if category == "database":
        reasons.append("bulk_data_and_referential_integrity")
    if size >= 100 * 1024 * 1024:
        reasons.append("large_asset_requires_staged_transfer")
    if not reasons:
        reasons.append("migration_semantics_unverified")
    level = "critical" if "credential_or_secret_material" in reasons else (
        "high" if any(x in reasons for x in ("identity_or_personal_data",
                                               "bulk_data_and_referential_integrity"))
        else "medium" if "business_confidentiality" in reasons else "low")
    return level, reasons


def _file_category(relative: str) -> str:
    if _IDENTITY.search(relative):
        return "identity"
    if _BUSINESS.search(relative):
        return "business_data"
    if relative.endswith((".db", ".sqlite", ".sqlite3")):
        return "database"
    if relative.endswith((".json", ".toml", ".yaml", ".yml", ".ini", ".env")):
        return "configuration"
    return "legacy_code_or_asset"


def _scan_files(root: Path) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    candidates: set[Path] = set()
    for candidate in (root / item for item in _LEGACY_ROOTS):
        if candidate.is_file() and not candidate.is_symlink():
            candidates.add(candidate)
        elif candidate.is_dir() and not candidate.is_symlink():
            for item in candidate.rglob("*"):
                if any(part in _EXCLUDED or part.startswith(".")
                       for part in item.relative_to(root).parts):
                    continue
                if item.is_file() and not item.is_symlink():
                    candidates.add(item)
    for item in sorted(candidates, key=lambda value: value.as_posix()):
        relative = item.relative_to(root).as_posix()
        size = item.stat().st_size
        category = _file_category(relative)
        level, reasons = _risk(relative, category, size)
        found.append({"path": relative, "bytes": size, "category": category,
                      "risk": level, "risk_reasons": reasons,
                      "inspection": "filesystem_metadata_only"})
    return found


def _sqlite_inventory(database: Path) -> dict[str, Any]:
    if not database.exists():
        return {"status": "not_found", "path": str(database), "tables": [],
                "views": [], "triggers": [], "total_rows": None, "bytes": None,
                "note": "No live legacy database supplied; counts remain unknown, not zero."}
    if database.is_symlink() or not database.is_file():
        raise ValueError("database must be a regular, non-symlink file")
    uri = f"{database.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        details = []
        identity_relations = []
        total_rows = 0
        for name in tables:
            quoted = '"' + name.replace('"', '""') + '"'
            columns = list(connection.execute(f"PRAGMA table_info({quoted})"))
            foreign_keys = list(connection.execute(f"PRAGMA foreign_key_list({quoted})"))
            index_rows = list(connection.execute(f"PRAGMA index_list({quoted})"))
            indexes = []
            for index in index_rows:
                index_name = str(index["name"])
                quoted_index = '"' + index_name.replace('"', '""') + '"'
                index_columns = list(connection.execute(f"PRAGMA index_xinfo({quoted_index})"))
                indexes.append({
                    "name": index_name,
                    "unique": bool(index["unique"]),
                    "origin": str(index["origin"]),
                    "partial": bool(index["partial"]),
                    "columns": [
                        {
                            "sequence": int(column["seqno"]),
                            "name": column["name"],
                            "expression": int(column["cid"]) == -2,
                            "descending": bool(column["desc"]),
                            "collation": column["coll"],
                            "key": bool(column["key"]),
                        }
                        for column in index_columns
                    ],
                })
            count = int(connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])
            total_rows += count
            column_names = [str(column["name"]) for column in columns]
            identity_columns = [column for column in column_names if _IDENTITY.search(column)]
            sensitive_columns = [column for column in column_names if _SENSITIVE.search(column)]
            business_columns = [column for column in column_names if _BUSINESS.search(column)]
            category = ("identity" if identity_columns else
                        "business_data" if business_columns else "database")
            risk, reasons = _risk(name, category, sensitive_columns=sensitive_columns)
            details.append({
                "name": name, "rows": count,
                "columns": [{"name": column["name"], "type": column["type"],
                             "not_null": bool(column["notnull"]),
                             "primary_key_order": int(column["pk"])} for column in columns],
                "identity_columns": identity_columns, "business_columns": business_columns,
                "sensitive_columns": sensitive_columns,
                "foreign_keys": [{"column": fk["from"], "references_table": fk["table"],
                                  "references_column": fk["to"], "on_delete": fk["on_delete"]}
                                 for fk in foreign_keys],
                "indexes": indexes,
                "risk": risk, "risk_reasons": reasons,
            })
            identity_targets = sorted({str(fk["table"]) for fk in foreign_keys
                                       if _IDENTITY.search(str(fk["table"]))})
            if identity_columns or identity_targets:
                identity_relations.append({
                    "table": name, "identity_columns": identity_columns,
                    "foreign_key_targets": identity_targets,
                    "raw_identifiers_exported": False,
                })
        objects = {
            kind + "s": [dict(row) for row in connection.execute(
                "SELECT name, tbl_name, sql FROM sqlite_master WHERE type=? ORDER BY name",
                (kind,))]
            for kind in ("view", "trigger")
        }
    return {"status": "scanned", "path": str(database), "bytes": database.stat().st_size,
            "tables": details, "views": objects["views"], "triggers": objects["triggers"],
            "index_count": sum(len(table["indexes"]) for table in details),
            "total_rows": total_rows, "identity_relations": identity_relations,
            "inspection": "sqlite_read_only_query_only", "raw_row_values_exported": False}


def build_legacy_inventory(root: str | Path,
                           database: str | Path | None = None) -> dict[str, Any]:
    """Build a deterministic read-only inventory; no file contents or row values are emitted."""
    base = Path(root).expanduser()
    if base.is_symlink() or not base.is_dir():
        raise ValueError("root must be a real directory")
    base = base.resolve()
    db_path = Path(database).expanduser() if database else base / "agency" / "state" / "agency.db"
    files = _scan_files(base)
    db = _sqlite_inventory(db_path)
    risk_counts: Counter[str] = Counter(item["risk"] for item in files)
    risk_counts.update(item["risk"] for item in db.get("tables", []))
    return {
        "schema_version": 1,
        "inventory_scope": "legacy_fde_runtime_and_local_state",
        "root": str(base),
        "generated_from": "read_only_metadata_and_sqlite_schema/counts",
        "source_mutated": False,
        "secrets_or_row_values_exported": False,
        "files": {"count": len(files), "total_bytes": sum(item["bytes"] for item in files),
                  "items": files},
        "database": db,
        "identity_relations": db.get("identity_relations", []),
        "business_mappings": list(_BUSINESS_MAPPINGS),
        "migration_candidates": inventory_report(),
        "risk_summary": dict(sorted(risk_counts.items())),
        "limitations": [
            "Missing database means table and row counts are unknown, never inferred as zero.",
            "Business mappings are hypotheses and require owner sign-off and semantic parity checks.",
            "Identity links are inferred from schema names and foreign keys; no raw identifiers are emitted.",
            "Filesystem scan records metadata only; nested secrets and data embedded in source/config text are not inspected.",
            "File inventory is limited to known legacy roots and excludes the new agent_platform implementation.",
        ],
    }
