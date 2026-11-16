# Agent Run token and cost budget policy

This document defines the C-72-01 budget vocabulary and the conservative defaults used when the selected tenant/project/Agent/Run scope has no explicit budget.

## Budget dimensions

| Field | Meaning | Window |
|---|---|---|
| `hard_input_tokens` | Maximum cumulative input tokens charged to the scope | UTC calendar month |
| `hard_context_window_tokens` | Maximum input-plus-planned-output allowance for one model request at this scope | Per model request |
| `hard_output_tokens` | Maximum cumulative output tokens charged to the scope | UTC calendar month |
| `hard_cost_usd` | Maximum cumulative model cost charged to the scope, in USD | UTC calendar month |

The legacy `hard_tokens` field remains the aggregate input-plus-output token ceiling. When both aggregate and split token limits are configured, all configured ceilings apply. Context-window limits are independent of monthly cumulative limits and are an upper bound only: the effective request limit must also respect the selected model's advertised context window.

## Safe defaults

Defaults are created and persisted on first use for the selected scope, so all workers share the same budget row. An authorized explicit budget for that exact scope and period replaces the default.

| Scope | Input tokens/month | Context window/request | Output tokens/month | Cost/month |
|---|---:|---:|---:|---:|
| Tenant | 500,000 | 32,768 | 100,000 | USD 25 |
| Project | 250,000 | 32,768 | 50,000 | USD 10 |
| Agent | 100,000 | 16,384 | 25,000 | USD 5 |
| Root Run | 50,000 | 16,384 | 12,000 | USD 1 |

All monthly limits use UTC calendar-month boundaries. Root Run scope IDs are the root Run IDs, not child Run IDs.

## Scope and failure behavior

- The quota API accepts tenant, project, Agent, and Run scopes; project/Agent/Run changes require the host's resource-scope authorizer.
- The runtime's configured quota-scope resolver selects the scope whose budget is reserved. This task defines the four scope budgets and defaults; it does not claim hierarchical multi-scope aggregation.
- No implicit defaults are created for model, queue, or worker-pool scopes. If one of those scopes is selected without an explicit budget, reservation remains fail-closed.
- A context-window default does not replace tokenizer estimation or the model-specific context-window check. Those request-time checks belong to C-72-02.
- These defaults are conservative platform guardrails, not provider price claims. Cost is measured against the host's configured price card.
