"""Tenant-scoped multi-variant evaluation matrices and report exports."""
from __future__ import annotations

import html
import json
import math
from datetime import datetime
from typing import Any, Mapping, Protocol, TypedDict
from uuid import uuid4


class EvaluationRunRow(TypedDict, total=False):
    eval_run_id: str
    model_key: str
    gate_passed: bool
    created_at: datetime | str | None
    report: Mapping[str, object] | str | None


class EvaluationVariant(TypedDict):
    eval_run_id: str
    model_key: str
    gate_passed: bool
    created_at: str
    metrics: dict[str, float]


class EvaluationComparisonRow(TypedDict):
    eval_run_id: str
    model_key: str
    delta_to_recommended: dict[str, float]


class EvaluationComparisonReport(TypedDict):
    report_id: str
    tenant_id: str
    dataset_id: str
    dataset_version: int
    variant_count: int
    metric_names: list[str]
    variants: list[dict[str, object]]
    comparisons_to_recommended: list[EvaluationComparisonRow]
    recommended: dict[str, str]
    created_by: str


class EvaluationComparisonRepository(Protocol):
    async def list_variant_runs(self, *, tenant_id: str, dataset_id: str,
                                dataset_version: int, limit: int) -> list[dict[str, Any]]: ...
    async def save_variant_report(self, *, tenant_id: str, report: EvaluationComparisonReport) -> str: ...
    async def load_variant_report(self, *, tenant_id: str, report_id: str) -> dict[str, Any]: ...


class PdfRenderer(Protocol):
    def render(self, report: Mapping[str, object]) -> bytes: ...


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"metric {name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"metric {name} must be finite")
    return result


def _metrics(run: Mapping[str, object]) -> dict[str, float]:
    payload = run.get("report") or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, Mapping):
        raise ValueError("evaluation report payload must be an object")
    values = {key: payload.get(key) for key in (
        "pass_rate", "average_score", "average_recall_at_k",
    )}
    extra = payload.get("metrics", {})
    if extra is not None:
        if not isinstance(extra, Mapping):
            raise ValueError("evaluation report metrics must be an object")
        values.update({str(key): value for key, value in extra.items()})
    return {
        str(key): _finite(value, str(key))
        for key, value in values.items()
        if value is not None
    }


class EvaluationComparisonService:
    """Build deterministic matrices and persist immutable report snapshots."""

    _QUALITY = ("average_score", "pass_rate", "average_recall_at_k")
    _EFFICIENCY = ("cost_usd", "cost_per_run", "latency_ms", "p50_latency_ms", "tokens")

    def __init__(self, *, repository: EvaluationComparisonRepository,
                 pdf_renderer: PdfRenderer | None = None) -> None:
        self.repository = repository
        self.pdf_renderer = pdf_renderer

    async def create_report(self, *, tenant_id: str, dataset_id: str, dataset_version: int,
                            created_by: str,
                            eval_run_ids: tuple[str, ...] | list[str] | None = None) -> dict[str, Any]:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not isinstance(dataset_id, str) or not dataset_id.strip() or len(dataset_id) > 128:
            raise ValueError("dataset_id must contain 1 to 128 characters")
        if isinstance(dataset_version, bool) or not isinstance(dataset_version, int) or dataset_version < 1:
            raise ValueError("dataset_version must be a positive integer")
        if not isinstance(created_by, str) or not created_by.strip():
            raise ValueError("created_by is required")
        if eval_run_ids is not None:
            if not isinstance(eval_run_ids, (tuple, list)) or any(
                not isinstance(item, str) or not item.strip() for item in eval_run_ids
            ):
                raise ValueError("eval_run_ids must contain non-empty strings")
            if len(eval_run_ids) != len(set(eval_run_ids)) or not 2 <= len(eval_run_ids) <= 32:
                raise ValueError("eval_run_ids must contain 2 to 32 unique run IDs")
        rows = await self.repository.list_variant_runs(
            tenant_id=tenant_id, dataset_id=dataset_id,
            dataset_version=dataset_version, limit=100,
        )
        if eval_run_ids is not None:
            by_id = {str(row["eval_run_id"]): row for row in rows}
            if set(eval_run_ids) - set(by_id):
                raise KeyError("evaluation runs not found in this tenant and dataset version")
            rows = [by_id[item] for item in eval_run_ids]
        if not 2 <= len(rows) <= 32:
            raise ValueError("at least two and at most 32 variant runs are required")
        variants: list[EvaluationVariant] = []
        for row in rows:
            created = row.get("created_at")
            variants.append({
                "eval_run_id": str(row["eval_run_id"]),
                "model_key": str(row["model_key"]),
                "gate_passed": row.get("gate_passed") is True,
                "created_at": created.isoformat() if isinstance(created, datetime) else str(created or ""),
                "metrics": _metrics(row),
            })
        common_quality = [name for name in self._QUALITY
                          if all(name in item["metrics"] for item in variants)]
        if not common_quality:
            raise ValueError("variants have no comparable quality metrics")

        def rank(item: EvaluationVariant) -> tuple[Any, ...]:
            metrics = item["metrics"]
            return (
                0 if item["gate_passed"] else 1,
                *(-metrics.get(name, -1.0) for name in self._QUALITY),
                *(metrics.get(name, math.inf) for name in self._EFFICIENCY),
                item["model_key"], item["eval_run_id"],
            )

        variants.sort(key=rank)
        recommended = variants[0]
        metric_names = sorted(set().union(*(item["metrics"].keys() for item in variants)))
        matrix = [{
            "eval_run_id": item["eval_run_id"], "model_key": item["model_key"],
            "gate_passed": item["gate_passed"],
            "metrics": {name: item["metrics"].get(name) for name in metric_names},
        } for item in variants]
        comparisons: list[EvaluationComparisonRow] = []
        for item in variants[1:]:
            deltas = {
                name: item["metrics"][name] - recommended["metrics"][name]
                for name in metric_names
                if name in item["metrics"] and name in recommended["metrics"]
            }
            comparisons.append({
                "eval_run_id": item["eval_run_id"], "model_key": item["model_key"],
                "delta_to_recommended": deltas,
            })
        report: EvaluationComparisonReport = {
            "report_id": str(uuid4()), "tenant_id": tenant_id,
            "dataset_id": dataset_id, "dataset_version": dataset_version,
            "variant_count": len(variants), "metric_names": metric_names,
            "variants": matrix, "comparisons_to_recommended": comparisons,
            "recommended": {
                "eval_run_id": recommended["eval_run_id"],
                "model_key": recommended["model_key"],
                "reason": (
                    "Gate pass first; then average score, pass rate and retrieval recall; "
                    "then cost, latency and token usage; ties resolve by model key and run ID."
                ),
            },
            "created_by": created_by,
        }
        await self.repository.save_variant_report(tenant_id=tenant_id, report=report)
        return report

    async def get_report(self, *, tenant_id: str, report_id: str) -> dict[str, Any]:
        if not tenant_id.strip() or not report_id.strip():
            raise ValueError("tenant_id and report_id are required")
        return await self.repository.load_variant_report(tenant_id=tenant_id, report_id=report_id)

    @staticmethod
    def render_html(report: Mapping[str, Any]) -> str:
        metrics = list(report.get("metric_names", []))
        headings = "".join(f"<th>{html.escape(str(name))}</th>" for name in metrics)
        rows = []
        best_id = report.get("recommended", {}).get("eval_run_id")
        for item in report.get("variants", []):
            cells = "".join(
                "<td>" + (
                    html.escape(f"{item.get('metrics', {}).get(name):.6g}")
                    if isinstance(item.get("metrics", {}).get(name), (int, float)) else "—"
                ) + "</td>" for name in metrics
            )
            marker = " <strong>Recommended</strong>" if item.get("eval_run_id") == best_id else ""
            rows.append(
                "<tr><td>" + html.escape(str(item.get("model_key", ""))) + marker
                + "</td><td>" + html.escape(str(item.get("eval_run_id", ""))) + "</td>"
                + "<td>" + ("pass" if item.get("gate_passed") else "fail") + "</td>"
                + cells + "</tr>"
            )
        title = html.escape(f"Evaluation comparison: {report.get('dataset_id', '')}")
        best = html.escape(str(report.get("recommended", {}).get("model_key", "")))
        reason = html.escape(str(report.get("recommended", {}).get("reason", "")))
        return (
            "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<title>{title}</title><style>body{{font:14px sans-serif;margin:32px}}"
            "table{border-collapse:collapse;width:100%}th,td{border:1px solid #ddd;padding:8px}"
            "th{background:#f3f4f6}strong{color:#146c43}</style></head><body>"
            f"<h1>{title}</h1><p>Dataset version: {report.get('dataset_version')}</p>"
            f"<h2>Recommended: {best}</h2><p>{reason}</p>"
            f"<table><thead><tr><th>Variant</th><th>Run</th><th>Gate</th>{headings}</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></body></html>"
        )

    def render_pdf(self, report: Mapping[str, Any]) -> bytes:
        renderer = self.pdf_renderer or ReportLabPdfRenderer()
        result = renderer.render(report)
        if not isinstance(result, bytes) or not result.startswith(b"%PDF-"):
            raise RuntimeError("PDF renderer returned an invalid document")
        return result


class ReportLabPdfRenderer:
    """Optional ReportLab renderer for PDF export."""

    def render(self, report: Mapping[str, Any]) -> bytes:
        try:
            from io import BytesIO

            from reportlab.lib import colors
            from reportlab.lib.pagesizes import landscape, letter
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        except ImportError as exc:
            raise RuntimeError("PDF export requires the optional reportlab dependency") from exc
        output = BytesIO()
        document = SimpleDocTemplate(output, pagesize=landscape(letter), title="Evaluation comparison")
        styles = getSampleStyleSheet()
        story = [Paragraph("Evaluation comparison", styles["Title"]),
                 Paragraph("Dataset " + html.escape(str(report.get("dataset_id"))), styles["Normal"]),
                 Spacer(1, 12),
                 Paragraph("Recommended: " + html.escape(str(
                     report.get("recommended", {}).get("model_key", "")
                 )), styles["Heading2"])]
        names = list(report.get("metric_names", []))
        data = [["Variant", "Run", "Gate", *names]]
        for item in report.get("variants", []):
            values = [item.get("model_key", ""), item.get("eval_run_id", ""),
                      "pass" if item.get("gate_passed") else "fail"]
            values.extend("" if item.get("metrics", {}).get(name) is None
                          else f"{item['metrics'][name]:.6g}" for name in names)
            data.append(values)
        table = Table(data, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8edf4")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
        ]))
        story.append(table)
        document.build(story)
        return output.getvalue()


class PostgresEvaluationComparisonRepository:
    """Persist report matrices and query source runs with tenant predicates."""

    def __init__(self, connection_factory: Any) -> None:
        self._connect = connection_factory

    async def list_variant_runs(self, *, tenant_id: str, dataset_id: str,
                                dataset_version: int, limit: int) -> list[dict[str, Any]]:
        if not 2 <= limit <= 100:
            raise ValueError("limit must be between 2 and 100")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT eval_run_id::text AS eval_run_id,model_key,report,gate_passed,created_at
                   FROM platform_eval_runs
                   WHERE tenant_id=%s AND dataset_id=%s AND dataset_version=%s
                   ORDER BY created_at DESC,eval_run_id LIMIT %s""",
                (tenant_id, dataset_id, dataset_version, limit),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def save_variant_report(self, *, tenant_id: str, report: EvaluationComparisonReport) -> str:
        payload = json.dumps(dict(report), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """INSERT INTO platform_eval_variant_reports
                   (report_id,tenant_id,dataset_id,dataset_version,variant_count,
                    recommended_run_id,report,created_by)
                   SELECT %s,run.tenant_id,run.dataset_id,run.dataset_version,%s,%s,%s::jsonb,%s
                   FROM platform_eval_runs run
                   WHERE run.eval_run_id=%s AND run.tenant_id=%s
                     AND run.dataset_id=%s AND run.dataset_version=%s
                   RETURNING report_id::text AS report_id""",
                (report["report_id"], report["variant_count"],
                 report["recommended"]["eval_run_id"], payload, report["created_by"],
                 report["recommended"]["eval_run_id"], tenant_id,
                 report["dataset_id"], report["dataset_version"]),
            )
            row = await cursor.fetchone()
        if row is None:
            raise KeyError("recommended evaluation run not found in this tenant and dataset version")
        return str(row["report_id"])

    async def load_variant_report(self, *, tenant_id: str, report_id: str) -> dict[str, Any]:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT report FROM platform_eval_variant_reports
                   WHERE tenant_id=%s AND report_id=%s""",
                (tenant_id, report_id),
            )
            row = await cursor.fetchone()
        if row is None:
            raise KeyError("evaluation comparison report not found in this tenant")
        value = row["report"]
        return json.loads(value) if isinstance(value, str) else dict(value)
