# Issue #763 — Approval and Evaluation Workbench

## Delivered increment

- Approval list is limited to the server's pending collection and displays action/resource, risk, requester, linked Run and expiry. Detail reads the authoritative approval resource and exposes the returned action_hash digest. Approve/reject/revoke continue to use the existing controlled server endpoints.
- The approval API currently does not return the original action parameter snapshot. The UI explicitly reports that limitation and does not reconstruct or fabricate parameter values.
- Versioned evaluation Dataset import supports the backend's JSONL/CSV format. It lists immutable versions, loads a selected version's cases/rubric/source reference, and links to the server export endpoint.
- Evaluation runs are pinned to the selected Dataset version and a caller-supplied configured model_key. Historical reports and baseline comparison use the existing observability API routes. Comparison creation sends dataset_id, dataset_version and the two eval_run_ids to the configured comparison service.
- Existing Knowledge RAG evaluation remains separate and unchanged.

## API wiring correction

create_platform_app previously accepted evaluation report/run services but did not accept or forward evaluation_dataset_repository, even though create_observability_router already implemented Dataset import/version routes. The app now accepts the repository, includes it in observability router configuration, requires the manager authorizer whenever evaluation services are configured, and exposes separate Dataset/Run/Report capability flags through workbench capabilities.

## API paths used

- POST /api/platform/v1/approvals/{approval_id}/decision
- POST /api/platform/v1/approvals/{approval_id}/revoke
- GET /api/platform/v1/approvals/{approval_id}
- POST /api/platform/v1/observability/evaluations/datasets/import
- GET /api/platform/v1/observability/evaluations/datasets/{dataset_id}/versions
- GET /api/platform/v1/observability/evaluations/datasets/{dataset_id}/versions/{version}
- GET /api/platform/v1/observability/evaluations/datasets/{dataset_id}/versions/{version}/export
- POST /api/platform/v1/observability/evaluations/run
- GET /api/platform/v1/observability/evaluations?dataset_id=...
- POST /api/platform/v1/observability/evaluations/comparisons (eval_run_ids contains baseline and candidate)

## Known gaps

- Approval parameter payload/snapshot is intentionally not exposed by the current GET contract; only action_hash is available. The backend should add a reviewed, redacted immutable snapshot contract before the UI displays parameter values.
- The approval list endpoint currently returns pending items only. The UI does not claim to provide a complete expired/rejected/revoked history view.
- Dataset discovery across all Dataset IDs is not supported by the current API; users must provide a Dataset ID to inspect its versions.
- Evaluation report history is optional and may return 501 when the configured repository does not implement latest_reports.
- Run detail currently returns summary metrics; per-case evidence depends on report repository capabilities and is not fabricated by the UI.
- APIs resolve an authenticated Principal and manager/reviewer authorizers. This increment does not claim auth=false support for approvals/evaluation endpoints; the module's dual-mode smoke tests remain part of #755/#752.
- No production server, model provider, external storage, or real-device testing was performed.

## Verification

A focused static contract test was added and registered in Platform Release Gate and Ruff. Local Python execution is unavailable in the connector-only editing environment; CI is the execution source and must be reported from the PR head. No test pass is claimed before CI runs.
