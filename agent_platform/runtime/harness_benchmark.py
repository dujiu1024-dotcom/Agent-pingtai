"""Deterministic, same-task benchmark runner for HarnessAdapter implementations."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any

from agent_platform.runtime.protocols import HarnessAdapter, HarnessRequest, HarnessResult


@dataclass(frozen=True)
class HarnessBenchmarkCase:
    case_id: str
    instructions: str
    input_text: str
    expected_contains: tuple[str, ...] = ()
    timeout_seconds: float = 120.0
    quality_threshold: float = 1.0
    streaming: bool = False
    required_tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("case_id must not be empty")
        if not self.input_text.strip():
            raise ValueError("input_text must not be empty")
        if not 0 < self.timeout_seconds <= 1800:
            raise ValueError("timeout_seconds must be greater than 0 and at most 1800")
        if not math.isfinite(self.quality_threshold) or not 0 <= self.quality_threshold <= 1:
            raise ValueError("quality_threshold must be between 0 and 1")



@dataclass(frozen=True)
class HarnessTokenPricing:
    input_usd_per_million: float
    output_usd_per_million: float

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.input_usd_per_million)
            or not math.isfinite(self.output_usd_per_million)
            or self.input_usd_per_million < 0
            or self.output_usd_per_million < 0
        ):
            raise ValueError("token pricing must be finite and non-negative")

    def estimate(self, usage: Mapping[str, int]) -> float:
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        if (
            isinstance(input_tokens, bool) or not isinstance(input_tokens, int)
            or isinstance(output_tokens, bool) or not isinstance(output_tokens, int)
            or input_tokens < 0 or output_tokens < 0
        ):
            raise ValueError("token usage must contain non-negative integers")
        return round(
            (
                input_tokens * self.input_usd_per_million
                + output_tokens * self.output_usd_per_million
            ) / 1_000_000,
            8,
        )


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
    quality_score: float = 0.0
    estimated_cost_usd: float | None = None
    attempts: int = 1
    recovered: bool = False
    time_to_first_token_ms: int | None = None
    streamed_events: int = 0
    error_type: str | None = None
    tool_calls_count: int = 0
    skipped: bool = False


@dataclass(frozen=True)
class HarnessBenchmarkReport:
    observations: tuple[HarnessBenchmarkObservation, ...]

    def summary(self) -> dict[str, dict[str, float | int]]:
        grouped: dict[str, list[HarnessBenchmarkObservation]] = {}
        for observation in self.observations:
            grouped.setdefault(observation.runtime, []).append(observation)
        result: dict[str, dict[str, float | int]] = {}
        for runtime, rows in grouped.items():
            eligible = [row for row in rows if not row.skipped]
            eligible_count = len(eligible)
            result[runtime] = {
                "cases": len(rows),
                "eligible_cases": eligible_count,
                "skipped_cases": sum(row.skipped for row in rows),
                "passed": sum(row.passed for row in eligible),
                "pass_rate": round(
                    sum(row.passed for row in eligible) / eligible_count, 4
                ) if eligible_count else 0.0,
                "average_duration_ms": round(
                    sum(row.duration_ms for row in eligible) / eligible_count, 2
                ) if eligible_count else 0.0,
                "total_input_tokens": sum(
                    row.usage.get("input_tokens", 0) for row in eligible
                ),
                "total_output_tokens": sum(
                    row.usage.get("output_tokens", 0) for row in eligible
                ),
                "average_quality_score": round(
                    sum(row.quality_score for row in eligible) / eligible_count, 4
                ),
                "recovered_cases": sum(row.recovered for row in eligible),
                "average_time_to_first_token_ms": round(
                    sum(
                        row.time_to_first_token_ms or 0
                        for row in eligible if row.time_to_first_token_ms is not None
                    ) / max(1, sum(row.time_to_first_token_ms is not None for row in eligible)),
                    2,
                ),
                "streamed_events": sum(row.streamed_events for row in eligible),
                "tool_calls": sum(row.tool_calls_count for row in eligible),
            }
            if any(row.estimated_cost_usd is not None for row in eligible):
                result[runtime]["estimated_cost_usd"] = round(
                    sum(row.estimated_cost_usd or 0 for row in eligible), 8
                )
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "observations": [asdict(row) for row in self.observations],
        }

    def to_markdown(self) -> str:
        lines = [
            "| Runtime | Pass rate (eligible) | Skipped | Quality | Avg latency (ms) | TTFT (ms) | Stream events | Input tokens | Output tokens | Est. cost (USD) |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for runtime, stats in sorted(self.summary().items()):
            lines.append(
                f"| {runtime} | {stats['passed']}/{stats['eligible_cases']} "
                f"({stats['pass_rate']:.0%}) | {stats['skipped_cases']} "
                f"| {stats['average_quality_score']:.2f} "
                f"| {stats['average_duration_ms']} | {stats['average_time_to_first_token_ms']} "
                f"| {stats['streamed_events']} | {stats['total_input_tokens']} "
                f"| {stats['total_output_tokens']} | "
                f"{stats.get('estimated_cost_usd', 'n/a')} |"
            )
        return "\n".join(lines)


RequestFactory = Callable[[str, HarnessBenchmarkCase], HarnessRequest]
GatewayToolVerifier = Callable[[str, HarnessRequest], bool]


class HarnessBenchmarkRunner:
    """Run each fixed case against each adapter sequentially for comparable inputs."""

    def __init__(
        self,
        *,
        adapters: Mapping[str, HarnessAdapter],
        request_factory: RequestFactory,
        pricing: Mapping[str, HarnessTokenPricing] | None = None,
        gateway_tool_verifier: GatewayToolVerifier | None = None,
        recovery_attempts: int = 0,
    ) -> None:
        if not adapters:
            raise ValueError("at least one Harness adapter is required")
        if any(not name.strip() for name in adapters):
            raise ValueError("runtime names must not be empty")
        if isinstance(recovery_attempts, bool) or not 0 <= recovery_attempts <= 5:
            raise ValueError("recovery_attempts must be between 0 and 5")
        self.adapters = dict(adapters)
        self.request_factory = request_factory
        self.pricing = dict(pricing or {})
        self.gateway_tool_verifier = gateway_tool_verifier
        self.recovery_attempts = recovery_attempts

    async def run(
        self, cases: Sequence[HarnessBenchmarkCase]
    ) -> HarnessBenchmarkReport:
        if not cases:
            raise ValueError("at least one benchmark case is required")
        observations: list[HarnessBenchmarkObservation] = []
        for case in cases:
            for runtime, adapter in self.adapters.items():
                request = self.request_factory(runtime, case)
                if case.required_tools and tuple(request.allowed_tools) != case.required_tools:
                    raise ValueError("benchmark request must expose exactly the case-required Gateway tools")
                if request.allowed_tools:
                    if self.gateway_tool_verifier is None:
                        raise ValueError(
                            "benchmark must not expose platform tools without a Gateway-only verifier"
                        )
                    if not self.gateway_tool_verifier(runtime, request):
                        raise ValueError("benchmark request tools are not verified as Gateway-only")
                stream_method = getattr(adapter, "stream", None)
                capabilities = getattr(adapter, "capabilities", None)
                if case.streaming and (
                    not callable(stream_method)
                    or not bool(getattr(capabilities, "streaming", False))
                ):
                    raise ValueError(
                        f"runtime {runtime} does not declare Harness streaming support"
                    )

                async def invoke_case():
                    if not case.streaming:
                        return await adapter.run(request), None, 0
                    chunks: list[str] = []
                    first_token_ms: int | None = None
                    streamed_events = 0
                    attempt_started = time.perf_counter()
                    final_result: HarnessResult | None = None
                    async for event in stream_method(request):
                        streamed_events += 1
                        event_type = getattr(event, "event_type", None)
                        if event_type == "text_delta":
                            delta = getattr(event, "text_delta", None)
                            if isinstance(delta, str) and delta:
                                if first_token_ms is None:
                                    first_token_ms = round(
                                        (time.perf_counter() - attempt_started) * 1000
                                    )
                                chunks.append(delta)
                        elif event_type == "completed":
                            event_result = getattr(event, "result", None)
                            if not isinstance(event_result, HarnessResult):
                                raise RuntimeError(
                                    "Harness completion event must include a normalized result"
                                )
                            if final_result is not None:
                                raise RuntimeError(
                                    "Harness stream emitted more than one completion event"
                                )
                            final_result = event_result
                    if final_result is None:
                        raise RuntimeError(
                            "Harness stream ended without a completion event"
                        )
                    if not final_result.output_text and chunks:
                        final_result = replace(final_result, output_text="".join(chunks))
                    return final_result, first_token_ms, streamed_events

                started = time.perf_counter()
                result = None
                time_to_first_token_ms: int | None = None
                streamed_events = 0
                last_error: Exception | None = None
                attempts = 0
                while attempts <= self.recovery_attempts:
                    attempts += 1
                    try:
                        result, time_to_first_token_ms, streamed_events = await asyncio.wait_for(
                            invoke_case(), timeout=case.timeout_seconds
                        )
                        last_error = None
                        break
                    except Exception as exc:
                        last_error = exc
                        if attempts > self.recovery_attempts:
                            break
                duration_ms = round((time.perf_counter() - started) * 1000)
                if result is None:
                    assert last_error is not None
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
                            attempts=attempts,
                            time_to_first_token_ms=time_to_first_token_ms,
                            streamed_events=streamed_events,
                            error_type=type(last_error).__name__,
                        )
                    )
                    continue

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
                quality_score = (
                    len(matched) / len(case.expected_contains)
                    if case.expected_contains else (1.0 if result.output_text.strip() else 0.0)
                )
                usage = dict(result.usage)
                pricing = self.pricing.get(runtime)
                estimated_cost = pricing.estimate(usage) if pricing is not None else None
                observations.append(
                    HarnessBenchmarkObservation(
                        runtime=runtime,
                        case_id=case.case_id,
                        passed=(
                            bool(result.output_text.strip())
                            and not missing
                            and quality_score >= case.quality_threshold
                        ),
                        duration_ms=duration_ms,
                        finish_reason=result.finish_reason,
                        usage=usage,
                        matched_expectations=matched,
                        missing_expectations=missing,
                        output_text=result.output_text,
                        quality_score=quality_score,
                        tool_calls_count=len(result.tool_calls),
                        estimated_cost_usd=estimated_cost,
                        attempts=attempts,
                        recovered=attempts > 1,
                        time_to_first_token_ms=time_to_first_token_ms,
                        streamed_events=streamed_events,
                    )
                )
        return HarnessBenchmarkReport(tuple(observations))
