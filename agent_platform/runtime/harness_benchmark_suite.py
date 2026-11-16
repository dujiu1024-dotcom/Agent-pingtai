"""Fixed, reproducible task suite and capability scorecard for Harness PoCs.

The suite contains no credentials and makes no network calls itself. Supply real
HarnessAdapter instances to run_fixed_harness_benchmark; CI uses deterministic
test adapters while provider-backed execution remains an explicit operator action.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from agent_platform.runtime.harness_benchmark import (
    HarnessBenchmarkCase,
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
) -> tuple[tuple[HarnessCandidateScorecard, ...], HarnessBenchmarkReport]:
    """Run the same fixed cases sequentially against each candidate."""
    scorecards = candidate_scorecards(adapters)
    report = await HarnessBenchmarkRunner(
        adapters=adapters,
        request_factory=request_factory,
        pricing=pricing,
        gateway_tool_verifier=gateway_tool_verifier,
        recovery_attempts=recovery_attempts,
    ).run(FIXED_HARNESS_CASES)
    return scorecards, report


def benchmark_evidence_payload(
    scorecards: Sequence[HarnessCandidateScorecard],
    report: HarnessBenchmarkReport,
) -> dict[str, Any]:
    """Serialize comparable metrics plus explicit unsupported capability evidence."""
    return {
        "suite_version": 1,
        "execution_mode": "provider-backed" if any(
            row.usage or row.estimated_cost_usd is not None for row in report.observations
        ) else "adapter-run",
        "candidate_capabilities": [asdict(item) for item in scorecards],
        "summary": report.summary(),
        "observations": [asdict(row) for row in report.observations],
        "limitations": [
            "Cancellation is reported as a declared capability; a provider-backed "
            "cancellation probe must be run separately before claiming process termination.",
            "Tool Gateway is capability-gated; use a run-scoped Gateway verifier for "
            "tool-enabled benchmark requests.",
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
