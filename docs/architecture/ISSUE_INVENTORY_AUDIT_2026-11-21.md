# GitHub Issue Inventory Audit — 2026-11-21

## Scope and method

- Repository: `dujiu1024-dotcom/Agent-pingtai`.
- Inventory query: all repository Issues, split into `is:open` and `is:closed`; 99 unique Issue numbers were returned.
- Every Issue body was fetched. Every Issue comment thread was fetched across the same 99 Issues; comments were scanned for status claims including pending/queued, unmerged PRs, blockers, unfinished work, duplicate references and parent/orphan language.
- This is a governance and traceability audit, not a code audit. Historical comments are not treated as current CI truth; PR status and timestamps must be refreshed before any task is counted complete.
- No Issue was reopened/closed and no PR was merged as part of this audit.

## Inventory snapshot

| State | Count | Issue numbers |
|---|---:|---|
| Open | 11 | #848, #870, #881–#889 |
| Closed | 88 | #2–#21, #71–#72, #79–#85, #262, #267, #517, #520–#528, #614, #665–#667, #742–#763, #797–#818 |
| **Total** | **99** | Unique Issues |

Open issues are not automatically incomplete; closed issues are not automatically complete. The state must be reconciled with acceptance checkboxes, linked PR state, CI and field evidence.

## Findings requiring action

### F1 — Closed Issues with unchecked acceptance items or unresolved delivery evidence

The following are high-confidence state/evidence drift candidates. Their current GitHub state is closed, while the current body retains unchecked items and/or the latest comments record pending PRs, blocked acceptance or unperformed field validation:

| Issue | Observed inconsistency | Required action |
|---|---|---|
| #8 | Closed; body has 2 unchecked items. Latest comments record PR #581 open/unmerged and all 12 checks queued at the time of that update. | Reconcile remaining A/C tasks against current PR/CI; reopen or explicitly split field-only acceptance. |
| #9 | Closed; body has 3 unchecked items; historical comments record unfinished Run lifecycle, budget and recovery acceptance. | Reconcile Task/Run, worker/replay and production integration evidence. |
| #10 | Closed; body has 4 unchecked items; comments record remaining ACL/API/real provider and retrieval acceptance. | Reconcile ACL list API, vector dimension, extraction source contract and real corpus/provider evidence. |
| #11 | Closed; latest comment says conflicts/expiry, user correction/export and real index cleanup remain. | Reopen or create explicit deployment/field acceptance child issue; do not count as fully complete. |
| #12 | Closed; body has 3 unchecked items; comments record PR #726 open/unmerged and production Gateway/Runtime acceptance gaps. | Reconcile production graph/Gateway and approval verifier contracts. |
| #13 | Closed; comments retain pending/blocked Blackboard conflict/recovery and event idempotency evidence. | Verify latest main, tests and production Redis recovery; correct issue state if still incomplete. |
| #14 | Closed; comments explicitly state browser E2E and desktop/mobile real-device acceptance were not performed. | Keep code completion separate from browser/device field acceptance. |
| #15 | Closed; body has 4 unchecked items; comments record open PR #506 and Vault/KMS/DB least-privilege field validation remaining. | Reconcile security items and external-system evidence. |
| #16 | Closed; comments record PR #732/#733 open/unmerged and production alert/trace wiring or field validation pending. | Verify latest PR/CI and production provider wiring. |
| #17 | Closed; comments record PR #735 open/unmerged and production Run worker quota admission field acceptance pending. | Verify worker admission, quota enforcement and live provider/storage evidence. |
| #18 | Closed; body has 4 unchecked items; latest comment records PR #737 open/unmerged and all seven workflows queued. | Reconcile backup/restore drills, Redis Outbox recovery and HA/DR field evidence. |
| #19 | Closed; comments record historical migration/security test gaps and skipped production evidence. | Confirm latest migration tests and restore missing regression coverage before declaring complete. |
| #20 | Closed; comments record PR #709 open/unmerged and real service integration still pending. | Reconcile API/Webhook/SDK lifecycle and real Vault/provider evidence. |
| #21 | Closed; comments record PR #500 open/unmerged and queued checks; production E2E/fault drills remain. | Reconcile the complete test matrix and production release evidence. |
| #267 | Closed; body retains unchecked items and 607 comments include recent failing frontend/identity/PG integration evidence. | Keep as evidence ledger but reopen or explicitly maintain an active successor if the two unchecked items remain required. |
| #614 | Closed; body has 8 checked/unchecked task markers with 2 unchecked; latest comment records PR #730 open/unmerged and CI pending. | Reconcile production composition and runtime lifecycle acceptance. |
| #665 | Closed; 5 checklist markers remain unchecked; latest comment says A-665-06 lacks paired real performance measurements and PR #718 is open. | Split automated parity guard from real performance acceptance and correct state. |
| #666 | Closed; 5 checklist markers with 4 unchecked; latest comment records PR #721 queued and full staging migration/zero-diff reconciliation not done. | Reconcile migration, rollback and archival DoD; do not infer completion from closure. |
| #667 | Closed; 5 checklist markers unchecked; latest comment records PR #728 open/unmerged and legacy panel parity not complete. | Reconcile API parity, browser fallback and first-paint/real-browser evidence. |

These are not automatic instructions to reopen every Issue. The owner should compare the latest main branch and current PR state, then either reopen the original Issue or create a narrowly scoped field-acceptance follow-up and link it.

### F2 — Parent/child relationship and scope traceability gaps

- #797–#813 are narrow P-UI-002 work items with very short bodies (roughly 122–380 characters) and no explicit Parent field in the body. Comments frequently say “code review passed / closed”, but the parent relation and specific acceptance evidence should be encoded in the body or GitHub sub-issue relationship.
- #814–#818 are P-UI-003 children; the body-level parent links are inconsistent. Their closure comments refer to PR #841, while earlier comments on #817 record PR #824 open/draft and CI failures. Verify that #841 truly subsumes every child’s acceptance scope and record the final merged SHA/CI evidence.
- #881–#883 point to PR #880 in their bodies but have no explicit parent relationship to #848/#884 and no acceptance heading. Add stable parent/scope and exact DoD fields.
- #870 is related to #848 and #742 but has no explicit parent declaration or dedicated acceptance criteria heading. Its scope is detailed, but it needs measurable DoD and traceability.
- No confirmed orphan was declared solely from a missing textual parent: the inventory contains cross-links in bodies/comments. These are orphan *candidates* requiring canonical parent relationships, not proof that the issues have no owner.

### F3 — Potential overlap / duplicate-scope candidates (not confirmed duplicates)

| Candidate set | Overlap | Required boundary |
|---|---|---|
| #517 vs #10/#11/#13 | Knowledge graph governance vs RAG, memory and Blackboard | Define graph asset governance/query responsibility and avoid duplicating retrieval/memory storage. |
| #520 vs #12/#16 | Prompt Lab/versioning vs runtime/prompt evaluation | Separate authoring/experimentation UI from production registry, policy and release gate. |
| #521 vs #10 | RAG testbench vs knowledge retrieval/evaluation | Keep testbench UX separate from ingestion, retrieval and evaluation service ownership. |
| #522 vs #8/#11 | Session persistence vs conversation and memory lifecycle | Separate session/message persistence from extracted memory lifecycle. |
| #523 vs #16/#747 | Model Eval vs evaluation/cost-quality operations | Define evaluation engine ownership and UI/analytics integration boundary. |
| #524 vs #20/#15 | Connector catalog/credentials vs extension and security lifecycle | Separate catalog discovery from connector runtime, secrets and authorization. |
| #525 vs #7/#20 | Skill/Tool registry vs core registry/extension lifecycle | Define packaging/install UX vs registry metadata, review and runtime enforcement. |
| #526/#527/#528 vs #9/#13/#14 | Replay UI, streaming and batch execution vs runtime/task/workbench | Separate user-facing workflow controls from execution engine semantics. |

These items are not marked duplicates. Existing closure/archival may be valid; ensure the canonical implementation Issue and acceptance evidence are linked before deleting or consolidating anything.

### F4 — Issue bodies with weak or missing explicit acceptance criteria

- #2–#4 and #80–#84 are archival/navigation wrappers; this is acceptable if they remain clearly marked as archived and point to canonical Issues.
- #797–#813 have short implementation notes rather than independently measurable acceptance criteria; their parent Issue/PR and CI evidence must be linked.
- #881–#883 and #870 need explicit measurable acceptance criteria and parent relationships.
- #8–#21 use a task/checkbox structure rather than the exact phrase “验收标准”; this is not by itself a defect, but the unchecked items and comments must be reconciled against current state.

## Confirmed good practices / coverage

- #848 remains open and has a substantial acceptance checklist; its D5 report correctly distinguishes audit artifacts from product implementation and production acceptance.
- #884 remains open and is the correct umbrella for requirements completeness and traceability; its body now links the new D4 and #267 work.
- #885–#889 each include explicit scope, acceptance criteria and deliverables. They cover cross-system business automation, AI ROI, knowledge asset governance, AI supply-chain trust and hybrid continuity.
- #742 and #743–#763 establish a structured frontend parent/child breakdown; their comments record Gate 0–3 delivery. Continue to require actual PR/CI evidence, not only a “delivered” comment.

## Required follow-up actions

1. Reconcile F1 closed-state mismatches with current main/PR/CI; do not bulk reopen or bulk close.
2. Add explicit parent links and measurable DoD to F2 candidates; use GitHub sub-issue relationships where supported.
3. Record canonical ownership boundaries for F3 overlap candidates; consolidate only after confirming no unique requirement is lost.
4. Add exact acceptance criteria to #870 and #881–#883; preserve #885–#889 criteria.
5. Update #267 stable task ledger and D4 matrix from the same canonical defect keys; every item needs code/test/PR/CI/field evidence.
6. Refresh all linked PR checks before changing any status. A queued, skipped or absent workflow is not a pass.

## Limitations

- The 99-Issue body and comment inventory is complete for the search snapshot, but the project may change after the snapshot.
- Comment scans include historical comments; a comment mentioning pending/failed work is a review signal, not proof the work is still pending today.
- This document does not establish product-code completion, production readiness or the truth of any external-system/field acceptance claim.

## Snapshot counts

- Open: 11; closed: 88; total: 99.
- All 99 Issue bodies fetched.
- All 99 Issue comment threads fetched; #267 alone contains 607 comments.
- Confirmed duplicate Issues: none from this pass. Potential overlaps are listed in F3.
- Confirmed orphan Issues: none established solely from textual data. Parent-link candidates are listed in F2.

## Reconciliation action — 2026-11-21

Following the per-Issue review of F1, 19 Issues were reopened because their current body retained unchecked acceptance items or their latest evidence explicitly left production/field acceptance incomplete:

- Core platform Issues: #8–#21 (14 Issues).
- Execution evidence ledger: #267.
- Production composition: #614.
- Migration/switch acceptance: #665–#667.

Each reopened Issue received a comment stating the specific unresolved acceptance boundary. Existing PRs cited by the historical comments were fetched directly and found merged; the old “PR open / CI queued” comments were stale. Merge status was therefore corrected in the assessment, but merged PRs were not treated as proof of full acceptance.

Current inventory state after reopening: 30 open Issues and 69 closed Issues (99 total). This count includes the 11 originally open Issues (#848, #870, #881–#889) plus the 19 reopened Issues listed above.

No PR was merged or closed in this reconciliation. No Issue was closed. Reopened Issues must remain open until their own remaining acceptance criteria are evidenced; field-only tasks should be split into linked follow-up Issues if they need independent ownership or scheduling.
