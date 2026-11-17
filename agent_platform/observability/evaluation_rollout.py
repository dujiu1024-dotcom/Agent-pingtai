"""Explicit, auditable bridge from Eval comparison reports to Prompt Lab rollouts."""
from __future__ import annotations

import inspect
import math
from collections.abc import Mapping
from typing import Any


class EvaluationPromptRolloutBridge:
    """Promote a persisted evaluation winner into an authorized Prompt rollout.

    The bridge never trusts a report supplied by the caller. It loads the report
    from the tenant-scoped comparison service and requires an explicit mapping
    from evaluation model keys to active Prompt Lab variant names.
    """

    def __init__(self, *, comparison_service: Any, rollout_service: Any) -> None:
        if not callable(getattr(comparison_service, "get_report", None)):
            raise ValueError("comparison_service must expose get_report()")
        if not callable(getattr(rollout_service, "configure", None)):
            raise ValueError("rollout_service must expose configure()")
        self.comparison_service = comparison_service
        self.rollout_service = rollout_service

    async def promote(
        self,
        *,
        tenant_id: str,
        report_id: str,
        prompt_id: str,
        model_to_prompt_variant: Mapping[str, str],
        baseline_model_key: str,
        candidate_percentage: float,
        actor_id: str,
        minimum_score_gain: float = 0.0,
    ) -> dict[str, Any]:
        for value, name in (
            (tenant_id, "tenant_id"), (report_id, "report_id"),
            (prompt_id, "prompt_id"), (actor_id, "actor_id"),
            (baseline_model_key, "baseline_model_key"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        if not isinstance(model_to_prompt_variant, Mapping) or not model_to_prompt_variant:
            raise ValueError("model_to_prompt_variant must be a non-empty mapping")
        if any(
            not isinstance(key, str) or not key.strip()
            or not isinstance(value, str) or not value.strip()
            for key, value in model_to_prompt_variant.items()
        ):
            raise ValueError("model_to_prompt_variant must map non-empty strings")
        if len(set(model_to_prompt_variant.values())) != len(model_to_prompt_variant):
            raise ValueError("each evaluation model must map to a distinct Prompt variant")
        if (
            isinstance(candidate_percentage, bool)
            or not isinstance(candidate_percentage, (int, float))
            or not math.isfinite(candidate_percentage)
            or not 1 <= candidate_percentage <= 100
        ):
            raise ValueError("candidate_percentage must be between 1 and 100")
        if (
            isinstance(minimum_score_gain, bool)
            or not isinstance(minimum_score_gain, (int, float))
            or not math.isfinite(minimum_score_gain)
            or minimum_score_gain < 0
        ):
            raise ValueError("minimum_score_gain must be finite and non-negative")

        report = self.comparison_service.get_report(
            tenant_id=tenant_id, report_id=report_id,
        )
        if inspect.isawaitable(report):
            report = await report
        if not isinstance(report, Mapping) or report.get("tenant_id") != tenant_id:
            raise PermissionError("evaluation report is outside the authenticated tenant")
        recommended = report.get("recommended")
        rows = report.get("variants")
        if not isinstance(recommended, Mapping) or not isinstance(rows, list) or len(rows) < 2:
            raise ValueError("evaluation report has no comparable variant matrix")
        winner_key = recommended.get("model_key")
        winner_run_id = recommended.get("eval_run_id")
        if not isinstance(winner_key, str) or not winner_key.strip():
            raise ValueError("evaluation report recommendation is invalid")
        if not isinstance(winner_run_id, str) or not winner_run_id.strip():
            raise ValueError("evaluation report recommendation has no source run")
        winner_rows = [
            row for row in rows
            if isinstance(row, Mapping)
            and row.get("model_key") == winner_key
            and row.get("eval_run_id") == winner_run_id
        ]
        if len(winner_rows) != 1:
            raise ValueError("recommended variant is not uniquely present in the report")
        winner = winner_rows[0]
        if winner.get("gate_passed") is not True:
            raise ValueError("recommended evaluation variant did not pass the evaluation gate")
        baseline_rows = [
            row for row in rows
            if isinstance(row, Mapping) and row.get("model_key") == baseline_model_key
        ]
        if len(baseline_rows) != 1 or baseline_model_key == winner_key:
            raise ValueError("baseline_model_key must identify one non-winning report variant")
        baseline = baseline_rows[0]
        winner_metrics = winner.get("metrics")
        baseline_metrics = baseline.get("metrics")
        if not isinstance(winner_metrics, Mapping) or not isinstance(baseline_metrics, Mapping):
            raise ValueError("evaluation report metrics are invalid")
        quality_metric = next(
            (
                name for name in ("average_score", "pass_rate")
                if name in winner_metrics and name in baseline_metrics
            ),
            None,
        )
        if quality_metric is None:
            raise ValueError("winner and baseline have no comparable quality metric")
        winner_score = winner_metrics[quality_metric]
        baseline_score = baseline_metrics[quality_metric]
        for value, label in ((winner_score, "winner score"), (baseline_score, "baseline score")):
            if (
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"{label} must be finite numeric")
        score_gain = float(winner_score) - float(baseline_score)
        if score_gain < minimum_score_gain:
            raise ValueError("recommended variant does not meet the minimum quality gain")

        if winner_key not in model_to_prompt_variant or baseline_model_key not in model_to_prompt_variant:
            raise ValueError("winner and baseline must both have explicit Prompt variant mappings")
        winner_variant = model_to_prompt_variant[winner_key]
        baseline_variant = model_to_prompt_variant[baseline_model_key]
        percentage = float(candidate_percentage)
        weights = {winner_variant: percentage}
        if percentage < 100:
            weights[baseline_variant] = 100.0 - percentage
        policy = await self.rollout_service.configure(
            tenant_id=tenant_id,
            prompt_id=prompt_id,
            weights=weights,
            actor=actor_id,
        )
        return {
            "tenant_id": tenant_id,
            "prompt_id": prompt_id,
            "report_id": report_id,
            "recommended_eval_run_id": winner_run_id,
            "recommended_model_key": winner_key,
            "baseline_model_key": baseline_model_key,
            "recommended_prompt_variant": winner_variant,
            "baseline_prompt_variant": baseline_variant,
            "quality_metric": quality_metric,
            "quality_gain": score_gain,
            "candidate_percentage": percentage,
            "weights": dict(policy.weights),
            "policy_revision": policy.revision,
            "updated_by": actor_id,
        }
