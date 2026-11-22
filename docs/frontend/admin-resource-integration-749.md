# Issue #749 — Admin resource integration contract

## Scope

This change is the parent-level integration slice for #749. It does not reimplement child-owned resource pages or create missing backend capabilities.

| Resource | Canonical route | Implementation owner | Main integration state |
|---|---|---:|---|
| Organization / RBAC | `/admin/organization`, `/admin/access-control` | #756 | Routes remain auth-mode gated; write APIs and full server-side lifecycle remain a backend/child acceptance gap |
| Agent definitions | `/admin/agents`, `/admin/agents/:agentId` | #757 | Existing Agent Definition page is reused |
| Workflow definitions | `/admin/workflows`, `/admin/workflows/:workflowId/edit` | #758 | Existing Workflow Designer and Graph Editor are reused; LangGraph compile/diagnostic/publish contract is not claimed |
| Model Provider | `/admin/models/providers` | #759 | List API uses registry kind `model_provider` |
| Model Template | `/admin/models/templates` | #759 | List API uses registry kind `model_template` |
| Model routing | `/admin/models/routing` | #759 | No dedicated routing-policy API verified; route intentionally has no guessed API mapping or fake write controls |
| Tool / MCP | `/admin/tools`, `/admin/tools/:toolId` | #759 | Dedicated Tool/Skill resource page is mounted; uses registry kind `tool` |
| Skill | `/admin/skills`, `/admin/skills/:skillId` | #759 | Dedicated Tool/Skill resource page is mounted; uses registry kind `skill` |

## Integration corrections

- Mount `ToolSkillResourcePage` for both Tool and Skill list/detail routes. The component already owns the corresponding Registry lifecycle calls; this PR only connects it to the canonical router.
- Correct Provider and Model Template list API paths to `/platform/v1/registry/model_provider` and `/platform/v1/registry/model_template`. The former aliases `providers` and `models` did not match the Registry kind contract.
- Remove guessed Tool, Skill, and routing-policy aliases from the generic API map. Tool and Skill use their dedicated page; model routing remains explicitly unavailable until a real API contract exists.
- Align route owner metadata to the child-work-package split in #749: #756 Organization/RBAC, #757 Agent, #758 Workflow, and #759 Provider/Template/routing/Tool/Skill.
- Add route-contract tests for no-auth reachability of non-identity resource pages, child ownership, and resource detail parameter matching.

## Verified boundaries / not claimed complete

- PR #923 is merged and supplies the Tool/MCP + Skill detail lifecycle component. Historical PRs #833 and #839 are not treated as delivery.
- The Workflow Designer and Graph Editor source are present on main and are mounted by the canonical routes. This does not prove LangGraph compiler diagnostics, immutable publish artifacts, or server-side graph validation. Those remain #758 acceptance items; no second workflow executor is introduced.
- Provider/Template Registry list access is wired to canonical Registry kinds. This does not implement SecretManager credential lifecycle UI, provider connection testing, model discovery, routing strategy CRUD/preview, or immutable provider/template pinning.
- Tool/MCP connection testing and health probing, and Skill compatibility evaluation, have no verified dedicated endpoints; the page must continue to show these as unavailable rather than fabricate results.
- Organization/RBAC is not promoted beyond its existing auth-only shell. No write API, audit, or complete auth-enabled authorization acceptance is claimed.
- The no-auth route tests verify frontend navigation only; they do not replace server-side auth/RBAC tests in both deployment modes.

## Validation

- Automated route contract tests added in `frontend/src/routes/route-config.test.ts`.
- GitHub Actions on the PR head is the CI authority. No real device, external provider, production server, or external resource validation is performed by this change.
