"""Deterministic, same-task benchmark runner for HarnessAdapter implementations."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from agent_platform.runtime.protocols import HarnessAdapter, HarnessRequest


@dataclass(frozen=True)
class HarnessBenchmarkCase:
    case_id: str
    instructions: str
    input_text: str
    expected_contains: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("case_id must not be empty")
        if not self.input_text.strip():
            raise ValueError("input_text must not be empty")


@dataclass(frozen=True)
class HarnessBenchmarkObservation:
    runtime: str
    case_id: str
    passed: bool
    duration_ms: int
    finish_reason: str
    usage: Mapping[str, int]
    matched_expectations: tuple[str, ...]
    missing_expectations: tuple[str, ...]
    output_text: str
    error_type: str | None = None


@dataclass(frozen=True)
class HarnessBenchmarkReport:
    observations: tuple[HarnessBenchmarkObservation, ...]

    def summary(self) -> dict[str, dict[str, float | int]]:
        grouped: dict[str, list[HarnessBenchmarkObservation]] = {}
        for observation in self.observations:
            grouped.setdefault(observation.runtime, []).append(observation)
        result: dict[str, dict[str, float | int]] = {}
        for runtime, rows in grouped.items():
            result[runtime] = {
                "cases": len(rows),
                "passed": sum(row.passed for row in rows),
                "pass_rate": round(sum(row.passed for row in rows) / len(rows), 4),
                "average_duration_ms": round(
                    sum(row.duration_ms for row in rows) / len(rows), 2
                ),
                "total_input_tokens": sum(
                    row.usage.get("input_tokens", 0) for row in rows
                ),
                "total_output_tokens": sum(
                    row.usage.get("output_tokens", 0) for row in rows
                ),
            }
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "observations": [asdict(row) for row in self.observations],
        }

    def to_markdown(self) -> str:
        lines = [
            "| Runtime | Pass rate | Avg latency (ms) | Input tokens | Output tokens |",
            "|---|---:|---:|---:|---:|",
        ]
        for runtime, stats in sorted(self.summary().items()):
            lines.append(
                f"| {runtime} | {stats['passed']}/{stats['cases']} "
                f"({stats['pass_rate']:.0%}) | {stats['average_duration_ms']} "
                f"| {stats['total_input_tokens']} | {stats['total_output_tokens']} |"
            )
        return "\n".join(lines)


RequestFactory = Callable[[str, HarnessBenchmarkCase], HarnessRequest]


class HarnessBenchmarkRunner:
    """Run each fixed case against each adapter sequentially for comparable inputs."""

    def __init__(
        self,
        *,
        adapters: Mapping[str, HarnessAdapter],
        request_factory: RequestFactory,
    ) -> None:
        if not adapters:
            raise ValueError("at least one Harness adapter is required")
        if any(not name.strip() for name in adapters):
            raise ValueError("runtime names must not be empty")
        self.adapters = dict(adapters)
        self.request_factory = request_factory

    async def run(
        self, cases: Sequence[HarnessBenchmarkCase]
    ) -> HarnessBenchmarkReport:
        if not cases:
            raise ValueError("at least one benchmark case is required")
        observations: list[HarnessBenchmarkObservation] = []
        for case in cases:
            for runtime, adapter in self.adapters.items():
                request = self.request_factory(runtime, case)
                if request.allowed_tools:
                    raise ValueError(
                        "benchmark requests must not expose platform tools before Gateway-only validation"
                    )
                started = time.perf_counter()
                try:
                    result = await adapter.run(request)
                    duration_ms = round((time.perf_counter() - started) * 1000)
                    normalized = result.output_text.casefold()
                    matched = tuple(
                        expected
                        for expected in case.expected_contains
                        if expected.casefold() in normalized
                    )
                    missing = tuple(
                        expected
                        for expected in case.expected_contains
                        if expected.casefold() not in normalized
                    )
                    observations.append(
                        HarnessBenchmarkObservation(
                            runtime=runtime,
                            case_id=case.case_id,
                            passed=bool(result.output_text.strip()) and not missing,
                            duration_ms=duration_ms,
                            finish_reason=result.finish_reason,
                            usage=dict(result.usage),
                            matched_expectations=matched,
                            missing_expectations=missing,
                            output_text=result.output_text,
                        )
                    )
                except Exception as exc:
                    duration_ms = round((time.perf_counter() - started) * 1000)
                    observations.append(
                        HarnessBenchmarkObservation(
                            runtime=runtime,
                            case_id=case.case_id,
                            passed=False,
                            duration_ms=duration_ms,
                            finish_reason="error",
                            usage={},
                            matched_expectations=(),
                            missing_expectations=case.expected_contains,
                            output_text="",
                            error_type=type(exc).__name__,
                        )
                    )
        return HarnessBenchmarkReport(tuple(observations))
