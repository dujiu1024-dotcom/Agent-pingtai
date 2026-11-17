"""Static production-composition regression checks for quota wiring."""
from __future__ import annotations

import ast
from pathlib import Path


def test_production_composition_uses_shared_quota_store_and_worker() -> None:
    source = Path("agent_platform/production.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "PostgresQuotaStore" in calls
    assert "QuotaService" in calls
    assert "QuotaReservationMaintenanceWorker" in calls

    production_factory = next(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "create_production_app"
    )
    factory_source = ast.get_source_segment(source, production_factory)
    assert factory_source is not None
    for required_binding in (
        "PostgresQuotaStore(connection_factory)",
        "QuotaService(quota_store)",
        "QuotaReservationMaintenanceWorker(quota_service)",
        "quota_reporting_store=quota_store",
        "quota_service=quota_service",
        "quota_reservation_worker=quota_reservation_worker",
        '"quota_schema": quota_schema_ready',
        '"quota.manage"',
    ):
        assert required_binding in factory_source


def test_production_readiness_fails_when_quota_schema_is_missing() -> None:
    source = Path("agent_platform/production.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    readiness = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "quota_schema_ready"
    )
    readiness_source = ast.get_source_segment(source, readiness)
    assert readiness_source is not None
    assert "to_regclass('public.platform_quota_budgets')" in readiness_source
    assert 'row["ready"]' in readiness_source
