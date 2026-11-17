# Prompt Lab A/B experiment engine (C-23-02)

## Contract

`PromptABTestService` evaluates 2–8 active variants for one tenant-scoped prompt
against one immutable `PromptExperimentDataset` version. Each case contains
strict prompt variables, expected/forbidden terms, and an optional natural
language rubric. Dataset/case/variable/output sizes are bounded.

Prompt versions are resolved by immutable version number from the existing
Prompt Registry. The engine calls an injected `PromptVariantExecutor`; it does
not bypass the Harness/Model Gateway or create provider credentials. An optional
`PromptJudge` provides LLM-as-judge scoring. Deterministic term scoring reuses
`observability.evaluation.evaluate_case`; aggregate comparisons reuse
`evaluation_governance.compare_metric_reports`.

## Execution and scoring

- Every case is run against every requested variant, producing a stable
  case-major matrix with variant/version, pass flag, rule score, optional judge
  score, blended score, latency, cost and sanitized error type.
- Prompt variable names must exactly match the immutable prompt version.
- The judge score must be finite and in [0, 1]. When present, final score is
  `(1 - judge_weight) * rule_score + judge_weight * judge_score`.
- Executor and judge calls have a bounded timeout; executor concurrency is
  limited. Failed cases are recorded with zero score and exception type only.
- Per-variant metrics include pass rate, average score, average latency and
  total cost. Every candidate is compared with the selected baseline with
  metric direction semantics from Evaluation Governance.
- Reports are stored under (tenant_id, experiment_id); a different tenant
  cannot retrieve the report.

## Integration boundaries

The registry is tenant-namespaced with `tenant_prompt_key`. Both the in-memory
registry and PostgreSQL registry expose immutable version resolution. Host
composition injects an executor that routes through the approved runtime and
an optional judge provider. A production deployment should supply a durable
`PromptExperimentRepository` and persistent dataset repository; the included
in-memory report repository is a reference/test adapter, not durable storage.

This engine provides reproducible orchestration and a comparison matrix. It
does not claim statistical significance, provider quality, or the A-23 repeated
run variance target without repeated production-like experiments.
