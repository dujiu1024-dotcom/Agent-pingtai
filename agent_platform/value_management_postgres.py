"""PostgreSQL persistence for enterprise AI value attribution."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable

from agent_platform.value_management import AIContribution, HumanBaseline, ValueResult


@dataclass(frozen=True)
class AttributionRecord:
    id: str


class PostgresAIValueRepository:
    """Tenant-scoped persistence adapter for value baselines and attribution."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    async def save_baseline(self, baseline: HumanBaseline) -> HumanBaseline:
        connection = await self._connection_factory()
        async with connection:
            await connection.execute(
                """INSERT INTO ai_value_baselines
                (tenant_id,process_id,role_id,version,manual_minutes_per_unit,
                 labor_cost_per_hour,quality_rate,sample_size,source,owner_id,
                 historical_business_volume,volume_period_start,volume_period_end,volume_source)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_id,process_id,role_id,version) DO NOTHING""",
                (baseline.tenant_id, baseline.process_id, baseline.role_id, baseline.version,
                 baseline.manual_minutes_per_unit, baseline.labor_cost_per_hour,
                 baseline.quality_rate, baseline.sample_size, baseline.source, baseline.owner_id,
                 baseline.historical_business_volume, baseline.volume_period_start,
                 baseline.volume_period_end, baseline.volume_source),
            )
            cursor = await connection.execute(
                """SELECT * FROM ai_value_baselines
                WHERE tenant_id=%s AND process_id=%s AND role_id=%s AND version=%s""",
                (baseline.tenant_id, baseline.process_id, baseline.role_id, baseline.version),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("baseline persistence failed")
        if (row["owner_id"] != baseline.owner_id or row["source"] != baseline.source
                or row["historical_business_volume"] != baseline.historical_business_volume
                or row["volume_period_start"] != baseline.volume_period_start
                or row["volume_period_end"] != baseline.volume_period_end
                or row["volume_source"] != baseline.volume_source):
            raise ValueError("baseline version is immutable; create a new version")
        return baseline

    async def get_baseline(
        self, *, tenant_id: str, process_id: str, role_id: str, version: str,
    ) -> HumanBaseline | None:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """SELECT * FROM ai_value_baselines
                WHERE tenant_id=%s AND process_id=%s AND role_id=%s AND version=%s""",
                (tenant_id, process_id, role_id, version),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return HumanBaseline(
            tenant_id=row["tenant_id"], process_id=row["process_id"], role_id=row["role_id"],
            manual_minutes_per_unit=Decimal(row["manual_minutes_per_unit"]),
            labor_cost_per_hour=Decimal(row["labor_cost_per_hour"]),
            quality_rate=Decimal(row["quality_rate"]), sample_size=row["sample_size"],
            source=row["source"], owner_id=row["owner_id"], version=row["version"],
            historical_business_volume=row["historical_business_volume"],
            volume_period_start=row["volume_period_start"], volume_period_end=row["volume_period_end"],
            volume_source=row["volume_source"],
        )

    async def save_attribution(
        self, *, tenant_id: str, process_id: str, role_id: str, baseline_version: str,
        actor_id: str, window_start: datetime, window_end: datetime,
        contribution: AIContribution, result: ValueResult, cost_source: str | None,
        business_evidence: Any | None, cost_basis: str,
    ) -> AttributionRecord:
        if window_start.tzinfo is None or window_end.tzinfo is None or window_end <= window_start:
            raise ValueError("window_start and window_end must be ordered timezone-aware timestamps")
        if cost_source is not None and (not isinstance(cost_source, str) or len(cost_source) > 128):
            raise ValueError("cost_source must be at most 128 characters")
        evidence = None if business_evidence is None else json.dumps(business_evidence, ensure_ascii=False)
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """INSERT INTO ai_value_attributions
                (tenant_id,process_id,role_id,baseline_version,window_start,window_end,
                 units_total,auto_completed,assisted_completed,human_takeovers,rework_units,
                 failed_units,actual_human_minutes,rework_human_minutes,agent_run_minutes,
                 saved_minutes,labor_savings,ai_cost,other_business_benefit,net_benefit,roi,
                 financial_status,cost_source,cost_basis,business_evidence,attribution_version,actor_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
                RETURNING id""",
                (tenant_id,process_id,role_id,baseline_version,window_start,window_end,
                 contribution.units_total,contribution.auto_completed,contribution.assisted_completed,
                 contribution.human_takeovers,contribution.rework_units,contribution.failed_units,
                 contribution.actual_human_minutes,contribution.rework_human_minutes,
                 contribution.agent_run_minutes,result.saved_minutes,result.labor_savings,
                 result.ai_cost,result.other_business_benefit,result.net_benefit,result.roi,
                 result.financial_status,cost_source,cost_basis,evidence,result.attribution_version,actor_id),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("attribution persistence failed")
        return AttributionRecord(id=str(row["id"]))

    async def summary(self, *, tenant_id: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """SELECT process_id,role_id,COUNT(*) AS sample_count,
                SUM(saved_minutes) AS saved_minutes,SUM(labor_savings) AS labor_savings,
                SUM(ai_cost) AS ai_cost,SUM(other_business_benefit) AS other_business_benefit,
                SUM(net_benefit) AS net_benefit,COUNT(*) FILTER
                  (WHERE financial_status='financially_confirmed') AS financially_confirmed_count,
                COUNT(*) FILTER (WHERE financial_status='estimated') AS estimated_count
                FROM ai_value_attributions
                WHERE tenant_id=%s AND window_start >= %s AND window_end <= %s
                GROUP BY process_id,role_id ORDER BY process_id,role_id""",
                (tenant_id,start,end),
            )
            rows = await cursor.fetchall()
        return [{
            "process_id": row["process_id"], "role_id": row["role_id"],
            "sample_count": row["sample_count"],
            "saved_minutes": str(row["saved_minutes"] or 0),
            "labor_savings": str(row["labor_savings"] or 0),
            "ai_cost": str(row["ai_cost"]) if row["ai_cost"] is not None else None,
            "other_business_benefit": str(row["other_business_benefit"]) if row["other_business_benefit"] is not None else None,
            "net_benefit": str(row["net_benefit"]) if row["net_benefit"] is not None else None,
            "financially_confirmed_count": row["financially_confirmed_count"],
            "estimated_count": row["estimated_count"],
        } for row in rows]
