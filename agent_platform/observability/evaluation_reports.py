"""Deterministic variant comparison and self-contained HTML/PDF reports."""
from __future__ import annotations

import html
import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


_METRICS = ("pass_rate", "average_score", "cost_usd", "total_tokens", "average_latency_ms", "p50_latency_ms", "p95_latency_ms")


def _number(value: Any, field: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ValueError(f"{field} must be finite and >= {minimum}")
    return result


def _cost(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("cost_usd must be a non-negative decimal or null") from None
    if not result.is_finite() or result < 0:
        raise ValueError("cost_usd must be a non-negative decimal or null")
    return result


def _normalize_variant(row: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise ValueError("variant result must be an object")
    name = row.get("model_variant")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("variant model_variant is required")
    cases = row.get("case_count")
    if isinstance(cases, bool) or not isinstance(cases, int) or cases < 1:
        raise ValueError("variant case_count must be a positive integer")
    passed = row.get("passed_count")
    if isinstance(passed, bool) or not isinstance(passed, int) or not 0 <= passed <= cases:
        raise ValueError("variant passed_count is invalid")
    pass_rate = _number(row.get("pass_rate"), "pass_rate")
    score = _number(row.get("average_score"), "average_score")
    if pass_rate > 1 or score > 1:
        raise ValueError("pass_rate and average_score must be <= 1")
    metrics = {
        "case_count": cases, "passed_count": passed,
        "pass_rate": pass_rate, "average_score": score,
        "input_tokens": row.get("input_tokens"),
        "output_tokens": row.get("output_tokens"),
        "total_tokens": row.get("total_tokens"),
        "cost_usd": _cost(row.get("cost_usd")),
        "average_latency_ms": _number(row.get("average_latency_ms"), "average_latency_ms"),
        "p50_latency_ms": _number(row.get("p50_latency_ms"), "p50_latency_ms"),
        "p95_latency_ms": _number(row.get("p95_latency_ms"), "p95_latency_ms"),
    }
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        value = metrics[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{field} must be a non-negative integer")
    return {"model_variant": name, **metrics}


def build_variant_comparison(run: Mapping[str, Any]) -> dict[str, Any]:
    """Build a stable matrix and explainable ranking from one persisted Eval Run."""
    if not isinstance(run, Mapping):
        raise ValueError("evaluation run must be an object")
    run_id, dataset_id, dataset_version = run.get("eval_run_id"), run.get("dataset_id"), run.get("dataset_version")
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", run_id):
        raise ValueError("eval_run_id has an invalid format")
    if not isinstance(dataset_id, str) or not dataset_id:
        raise ValueError("dataset_id is required")
    if isinstance(dataset_version, bool) or not isinstance(dataset_version, int) or dataset_version < 1:
        raise ValueError("dataset_version must be a positive integer")
    raw = run.get("variants")
    if not isinstance(raw, list) or not raw:
        raise ValueError("evaluation run has no variant results")
    variants = [_normalize_variant(item) for item in raw]
    names = [item["model_variant"] for item in variants]
    if len(names) != len(set(names)):
        raise ValueError("evaluation run contains duplicate model variants")
    # Quality is primary; ties prefer known lower cost, latency, tokens, then lexical model key.
    ranked = sorted(variants, key=lambda item: (
        -item["average_score"], -item["pass_rate"],
        item["cost_usd"] is None,
        item["cost_usd"] if item["cost_usd"] is not None else Decimal("0"),
        item["average_latency_ms"], item["total_tokens"], item["model_variant"],
    ))
    winner = ranked[0]
    runner_up = ranked[1] if len(ranked) > 1 else None
    reasons = [
        f"质量优先：平均评分 {winner['average_score']:.3f}，通过率 {winner['pass_rate']:.1%}",
        f"平均延迟 {winner['average_latency_ms']:.1f} ms，总 Token {winner['total_tokens']}",
    ]
    if winner["cost_usd"] is None:
        reasons.append("该 variant 未提供完整成本数据，成本不参与比较")
    else:
        reasons.append(f"已知总成本 USD {winner['cost_usd']}")
    if runner_up:
        reasons.append(f"相较第二名 {runner_up['model_variant']}，平均评分差 {winner['average_score'] - runner_up['average_score']:+.3f}")
    return {
        "eval_run_id": run_id, "dataset_id": dataset_id, "dataset_version": dataset_version,
        "variant_count": len(variants), "metrics": list(_METRICS),
        "matrix": [{
            "rank": rank, **item,
            "cost_usd": str(item["cost_usd"]) if item["cost_usd"] is not None else None,
        } for rank, item in enumerate(ranked, start=1)],
        "recommendation": {
            "model_variant": winner["model_variant"], "reason": reasons,
            "ranking_policy": "average_score desc, pass_rate desc, known cost asc, average latency asc, total tokens asc, model key asc",
        },
    }


def render_variant_report_html(comparison: Mapping[str, Any]) -> str:
    headers = ("排名", "Variant", "通过率", "平均评分", "成本 USD", "Input Token", "Output Token", "Total Token", "平均延迟 ms", "P95 ms")
    head = "".join(f"<th scope=\"col\">{html.escape(label)}</th>" for label in headers)
    rows = []
    for item in comparison["matrix"]:
        values = (
            str(item["rank"]), item["model_variant"], f"{item['pass_rate']:.1%}",
            f"{item['average_score']:.3f}", item["cost_usd"] if item["cost_usd"] is not None else "未知",
            str(item["input_tokens"]), str(item["output_tokens"]), str(item["total_tokens"]),
            f"{item['average_latency_ms']:.1f}", f"{item['p95_latency_ms']:.1f}",
        )
        rows.append("<tr>" + "".join(f"<td>{html.escape(value)}</td>" for value in values) + "</tr>")
    recommendation = comparison["recommendation"]
    reasons = "".join(f"<li>{html.escape(reason)}</li>" for reason in recommendation["reason"])
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Model Eval Variant Report</title><style>"
        "body{font:14px/1.6 system-ui,sans-serif;margin:32px;color:#172033}"
        "h1,h2{line-height:1.25}table{border-collapse:collapse;width:100%;margin-top:16px}"
        "th,td{border:1px solid #ccd3df;padding:8px;text-align:left}th{background:#f2f5f9}"
        "section{margin:24px 0}code{overflow-wrap:anywhere}</style></head><body>"
        "<h1>Model Eval Variant Report</h1>"
        f"<p>Eval Run: <code>{html.escape(comparison['eval_run_id'])}</code> · Dataset: "
        f"<code>{html.escape(comparison['dataset_id'])}</code> v{comparison['dataset_version']}</p>"
        f"<section><h2>最佳推荐：{html.escape(recommendation['model_variant'])}</h2><ul>{reasons}</ul></section>"
        f"<section><h2>Variant 对比矩阵</h2><table><thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></section>"
        "<p>排序策略：质量优先；同分时依次比较已知成本、平均延迟、Token 用量和 Variant 名称。"
        "缺失成本显示为未知，不进行估算。</p></body></html>"
    )


def render_variant_report_pdf(comparison: Mapping[str, Any]) -> bytes:
    """Create a dependency-free, single-page PDF summary using the built-in Helvetica font."""
    recommendation = comparison["recommendation"]
    lines = [
        "Model Eval Variant Report", f"Eval Run: {comparison['eval_run_id']}",
        f"Dataset: {comparison['dataset_id']} v{comparison['dataset_version']}", "",
        f"Recommended: {recommendation['model_variant']}", *recommendation["reason"], "",
        "Rank | Variant | Pass rate | Avg score | Cost USD | Input | Output | Total | Avg ms | P95 ms",
    ]
    for item in comparison["matrix"]:
        cost = item["cost_usd"] if item["cost_usd"] is not None else "unknown"
        lines.append(
            f"{item['rank']} | {item['model_variant']} | {item['pass_rate']:.1%} | "
            f"{item['average_score']:.3f} | {cost} | {item['input_tokens']} | "
            f"{item['output_tokens']} | {item['total_tokens']} | "
            f"{item['average_latency_ms']:.1f} | {item['p95_latency_ms']:.1f}"
        )
    commands = ["BT", "/F1 9 Tf", "48 790 Td", "12 TL"]
    for line in lines[:55]:
        text = "".join(char if char.isprintable() else " " for char in line)
        text = text.encode("latin-1", "replace").decode("latin-1")
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.extend((f"({escaped}) Tj", "T*"))
    commands.append("ET")
    stream = "\n".join(commands).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    return bytes(output)
