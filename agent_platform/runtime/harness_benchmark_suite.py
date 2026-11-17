"""Fixed, reproducible task suite and capability scorecard for Harness PoCs.

The suite contains no credentials and makes no network calls itself. Supply real
HarnessAdapter instances to run_fixed_harness_benchmark; CI uses deterministic
test adapters while provider-backed execution remains an explicit operator action.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Awaitable, Callable, Mapping, Sequence

from agent_platform.runtime.harness_benchmark import (
    HarnessBenchmarkCase,
    HarnessBenchmarkObservation,
    HarnessBenchmarkReport,
    HarnessBenchmarkRunner,
)
from agent_platform.runtime.protocols import HarnessAdapter


@dataclass(frozen=True)
class HarnessCandidateScorecard:
    candidate: str
    streaming: bool
    cancellation: bool
    timeout: bool
    resume: bool
    tool_gateway: bool
    cancellation_probe_passed: bool | None = None

    @classmethod
    def from_adapter(cls, name: str, adapter: HarnessAdapter) -> "HarnessCandidateScorecard":
        capabilities = getattr(adapter, "capabilities", None)
        if capabilities is None:
            raise ValueError(f"candidate {name} must declare Harness capabilities")
        return cls(
            candidate=name,
            streaming=bool(getattr(capabilities, "streaming", False)),
            cancellation=bool(getattr(capabilities, "cancellation", False)),
            timeout=bool(getattr(capabilities, "timeout", False)),
            resume=bool(getattr(capabilities, "resume", False)),
            tool_gateway=bool(getattr(capabilities, "tool_gateway", False)),
        )


# Fixed prompts and exact-match anchors keep candidate inputs identical. These are
# public/synthetic tasks; never place customer data or secrets in this dataset.
FIXED_HARNESS_CASES: tuple[HarnessBenchmarkCase, ...] = (
    HarnessBenchmarkCase(
        "fact-extraction", "Extract the stated metric without inference.",
        "Quarterly report: revenue increased by 12% year over year.",
        ("revenue", "12%"), quality_threshold=1.0,
    ),
    HarnessBenchmarkCase(
        "evidence-grounding", "Return the evidence identifier supporting the claim.",
        "Claim: latency fell. Evidence register: E-104 records the latency reduction.",
        ("E-104",), quality_threshold=1.0,
    ),
    HarnessBenchmarkCase(
        "structured-reasoning", "Compute the total and show the arithmetic.",
        "There are 17 queued jobs and 8 running jobs. State the total.",
        ("25",), quality_threshold=1.0,
    ),
    HarnessBenchmarkCase(
        "instruction-following", "Answer with exactly the requested category.",
        "Classify this event: HTTP request exceeded its deadline.",
        ("timeout",), quality_threshold=1.0,
    ),
    HarnessBenchmarkCase(
        "long-context-recall", "Return the project code associated with the owner.",
        "Notes: unrelated item A. Owner Lin manages project ORBIT-27. "
        "Unrelated item B. Return only the project code.",
        ("ORBIT-27",), quality_threshold=1.0,
    ),
    HarnessBenchmarkCase(
        "streaming-response", "Summarize the status in one sentence.",
        "Deployment completed; health checks are passing.",
        ("completed",), quality_threshold=1.0, streaming=True,
    ),
    HarnessBenchmarkCase(
        "gateway-tool-call",
        "Call the approved knowledge.search tool exactly once. Return its result.",
        "Use the available approved tool; do not simulate a tool call in text.",
        ("benchmark-tool-result",),
        required_tools=("knowledge.search",),
    ),
)


def candidate_scorecards(
    adapters: Mapping[str, HarnessAdapter],
) -> tuple[HarnessCandidateScorecard, ...]:
    if not adapters:
        raise ValueError("at least one candidate adapter is required")
    return tuple(
        HarnessCandidateScorecard.from_adapter(name, adapter)
        for name, adapter in sorted(adapters.items())
    )


async def run_fixed_harness_benchmark(
    *,
    adapters: Mapping[str, HarnessAdapter],
    request_factory: Any,
    pricing: Mapping[str, Any] | None = None,
    gateway_tool_verifier: Any = None,
    recovery_attempts: int = 1,
    cancellation_probes: Mapping[str, Callable[[HarnessAdapter], Awaitable[bool]]] | None = None,
) -> tuple[tuple[HarnessCandidateScorecard, ...], HarnessBenchmarkReport]:
    """Run the same fixed cases sequentially against each candidate."""
    scorecards = candidate_scorecards(adapters)
    probe_results: dict[str, bool | None] = {}
    for name, adapter in sorted(adapters.items()):
        probe = (cancellation_probes or {}).get(name)
        probe_results[name] = bool(await probe(adapter)) if probe is not None else None
    scorecards = tuple(
        replace(item, cancellation_probe_passed=probe_results[item.candidate])
        for item in scorecards
    )
    observations: list[HarnessBenchmarkObservation] = []
    for name, adapter in sorted(adapters.items()):
        capabilities = getattr(adapter, "capabilities", None)
        cases = tuple(
            case for case in FIXED_HARNESS_CASES
            if (
                (not case.streaming or bool(getattr(capabilities, "streaming", False)))
                and (not case.required_tools or bool(getattr(capabilities, "tool_gateway", False)))
            )
        )
        candidate_report = await HarnessBenchmarkRunner(
            adapters={name: adapter},
            request_factory=request_factory,
            pricing={name: pricing[name]} if pricing and name in pricing else None,
            gateway_tool_verifier=gateway_tool_verifier,
            recovery_attempts=recovery_attempts,
        ).run(cases)
        observations.extend(candidate_report.observations)
    return scorecards, HarnessBenchmarkReport(tuple(observations))


def benchmark_evidence_payload(
    scorecards: Sequence[HarnessCandidateScorecard],
    report: HarnessBenchmarkReport,
) -> dict[str, Any]:
    """Serialize comparable metrics plus explicit unsupported capability evidence."""
    return {
        "suite_version": 1,
        "execution_mode": "caller-supplied-adapters",
        "candidate_capabilities": [asdict(item) for item in scorecards],
        "summary": report.summary(),
        "observations": [asdict(row) for row in report.observations],
        "limitations": [
            "Cancellation capability and cancellation_probe_passed are separate: only a supplied "
            "provider-backed probe can verify that cancellation terminates work.",
            "The gateway-tool-call case requires a request factory to expose exactly "
            "knowledge.search and a run-scoped Gateway verifier; tool_calls counts actual normalized calls.",
            "Estimated cost is computed only when explicit per-candidate token pricing is supplied.",
            "Exact-match quality anchors are deterministic screening metrics, not human preference evaluation.",
        ],
    }


__all__ = [
    "FIXED_HARNESS_CASES",
    "HarnessCandidateScorecard",
    "benchmark_evidence_payload",
    "candidate_scorecards",
    "run_fixed_harness_benchmark",
]
