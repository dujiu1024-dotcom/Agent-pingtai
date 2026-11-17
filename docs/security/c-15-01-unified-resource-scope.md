# C-15-01 unified tenant/resource scope adapters

## Scope contract
ResourceScope is the common authorization request: tenant, resource type, resource ID and permission. ResourceScopeAuthorizer rejects tenant mismatch before consulting a policy backend, then delegates human/API principals to IdentityService and machine principals to ServiceIdentityService. Both branches require the persisted resource grant; service credentials additionally require the matching credential scope.

## Entry-point adapters
- API and management handlers call authorize(principal, ResourceScope(...)) with the authenticated principal and server-resolved resource. Never construct a principal from request body/header values.
- Tool Gateway and MCP use gateway_authorizer as the Gateway resource ACL callback. MCP and LangGraph/Strands tools remain Gateway-only; MCP has no independent authorization bypass.
- Harness media uses harness_media_resolver and harness_media_output_writer; authorization completes before object-store read/write.
- Knowledge retrieval builds principals from persisted tenant membership/RBAC. API credentials are restricted to the intersection of credential scopes and RBAC permissions.

## Existing enforcement retained
Gateway construction requires resource resolver plus authorizer and recalculates resource from trusted tool arguments. Knowledge PostgreSQL queries apply tenant/project/private ACL filters in SQL. File download and signed URL paths authorize file/session/project access before object storage access. Management routers continue to require configured authorizer callbacks; use the common adapter to avoid divergent policy semantics.

## Verification boundary
Unit tests exercise cross-tenant denial, exact resource binding, Gateway actor binding, and Harness media read/write ordering. CI validates code and tests only. No live server, external resource, production identity provider, or object store was requested or exercised.
