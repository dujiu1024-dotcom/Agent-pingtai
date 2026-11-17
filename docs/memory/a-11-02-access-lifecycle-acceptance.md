# Issue #11 / A-11-02 memory access and lifecycle acceptance

This acceptance suite verifies the memory retrieval contract at the service boundary.

## Coverage

- User-private memory is visible only to its owner.
- Project memory requires project membership; agent memory requires agent membership.
- Tenant memory is visible only inside its tenant; a different tenant receives no records.
- The service-level retrieval API and the policy-driven retrieval API agree on scope filtering.
- Expired, archived, and forgotten memories are excluded from retrieval.
- Corrections require scope-management authorization, increment the version, and make the corrected content/source the current retrieval result.

## Run

```bash
python -m unittest tests.test_issue_11_a_11_02_memory_access_lifecycle -v
ruff check tests/test_issue_11_a_11_02_memory_access_lifecycle.py
```

The suite uses the in-memory repository and deterministic fixture data. It is an automated contract test, not evidence of a live multi-tenant PostgreSQL deployment or production authorization-provider configuration.
