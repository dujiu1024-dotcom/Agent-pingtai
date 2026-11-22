# Business management hub — Issue #750 / L3-T14

## Purpose

The business management hub is an integration-only entry point. It links to the single-owner pages for Knowledge (#760), Memory (#761), Sessions/Files (#762), and Approval/Evaluation (#763). It does not create duplicate CRUD, duplicate caches, or client-side authorization decisions.

## Route and ownership

- Route: `/operations/business-management`
- Navigation: Intelligent Agent Collaboration section → Business Management
- Page: `frontend/src/features/business/BusinessManagementHub.tsx`
- Route registration: `frontend/src/routes/route-config.ts`
- Route mounting: `frontend/src/App.tsx`

## Domain entry points

| Domain | Route | Owner | Responsibility |
|---|---|---|---|
| Knowledge / RAG | `/operations/knowledge-bases` | #760 | Collections, documents, chunks, retrieval and citations |
| Memory | `/operations/memories` | #761 | Candidate/confirmed memory governance and history |
| Sessions / Messages | `/operations/conversations` | #762 | Session and message lifecycle |
| Files / Artifacts | `/operations/files` | #762 | File metadata, versions and authorized access |
| Approvals | `/operations/approvals` | #763 | Controlled decisions and approval details |
| Evaluation | `/operations/evaluation/datasets` | #763 | Dataset versions, cases and evaluation runs |

## Cross-domain journeys

- Knowledge evidence: collection → document → chunk/search hit → citation source. Knowledge Workbench owns the underlying data and deep links.
- Session and attachment: session → messages → associated file → file metadata/version. Session/File implementation remains exclusively owned by #762.
- Approval and evaluation: approval request → approval detail/decision → dataset version → evaluation run/result. Approval/Evaluation entities remain exclusively owned by #763.

The hub contains navigation only. It does not prefetch entity bodies, persist sensitive data, infer ACL grants, or fabricate missing cross-entity links.

## Security and API boundaries

- Backend remains authoritative for Knowledge ACL, memory sensitivity, session visibility, approval decisions and file access.
- File download must continue through the owning File UI/API and server-side authorization; this hub never creates or exposes storage URLs.
- auth=false/auth=true behavior is inherited from the canonical route/auth provider and each owning API. This navigation-only page does not add a separate auth policy.
- Streaming/SSE recovery, file range/expiry/download authorization, and complete cross-entity ACL traversal remain acceptance responsibilities of #752/#755 and the owning sub-Issues.
- Existing Approval/Evaluation API limitations documented in `approval-evaluation-workbench.md` remain unchanged.

## Verification boundary

The hub is a client-side route and navigation composition. PR CI is the source of build/typecheck/test results. No real-device, production server, external storage, or external model validation is claimed.
