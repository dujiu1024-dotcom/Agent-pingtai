# Connector framework and authentication contract

Issue #27 / C-27-03 standardizes the connector boundary without replacing provider-specific
transport or data mapping.

## Shared contract

- `agent_platform.connectors.BaseConnector` owns connector identity, credential references,
  authentication and lifecycle hooks.
- New integrations use `SecretStoreConnectorAuthenticator`. It resolves an opaque
  `secret://{tenant}/credentials/{id}` reference just in time and binds the lookup to tenant,
  consumer type/id, actor and required scope tags.
- Authentication fails closed when the reference, tenant, scope grant, resolver or returned
  token is invalid. Provider exceptions are sanitized; the base class does not retain the
  resolved token.
- The legacy injected resolver remains a compatibility bridge for existing integrations.
  New connectors should not add a second credential-resolution path.

## Existing connector migration

| Connector | Shared base | Provider-specific behavior retained |
|---|---|---|
| Google Drive knowledge connector | `GoogleDriveKnowledgeConnector` | Changes API cursor, file export/download limits, ACL-aware document factory |
| SharePoint knowledge connector | `SharePointKnowledgeConnector` | Graph delta cursor, permission mapping, signed download URL checks, host-owned grant revocation |
| Configurable JSON HTTP connector | `HTTPJSONConnectorProvider` | Host-configured HTTPS endpoints, allowlisted hosts, bounded pagination and response validation |

The data-connector service detects BaseConnector providers and invokes their shared
authenticator using the connection tenant and declared scopes. It does not fall back to the
legacy secret resolver for a migrated provider.

## Regression gate

`tests/test_connector_framework.py` covers tenant/consumer/actor/scope binding, cross-tenant
and empty-scope rejection, sanitized fail-closed behavior, all three migrated connector types,
the data-connector service integration, legacy resolver compatibility and required
configuration. The module is included in Ruff and the Platform Release Gate unit suite.
