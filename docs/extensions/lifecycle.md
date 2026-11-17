# Extension/plugin lifecycle

Extensions are declarative packages. `ExtensionManifest` validates the extension ID, semantic version, platform API version, entrypoint, unique permission declarations, dependency IDs and exact semantic-version pins. The ZIP package utility rejects traversal, symlinks, special files, duplicate/case-colliding paths, oversized archives and unsafe compression ratios; detached Ed25519 signatures are checked against a trusted publisher key before extraction.

## Lifecycle manager
`ExtensionLifecycleManager` coordinates install, enable, upgrade, disable and uninstall. The state store tracks the installed manifest, enabled/disabled state and verified artifact reference. Dependency installation is deterministic and dependency-first; uninstall is dependants-first and blocks removal while active dependants remain unless cascade is explicitly requested.

## Authorization and audit
Every declared permission is checked before an operation begins. Every mutating step writes an audit intent before calling the runtime. Missing/failing authorization or audit prevents side effects. Runtime and registry failures trigger compensating actions for completed steps. Compensation failures are not silently treated as success; inspect the audit trail and reconcile the durable state.

## Upgrade and rollback
Upgrades require a strictly greater semantic version, compatible platform API, exact dependency pins, a verified replacement artifact and the prior verified artifact for rollback. The runtime performs the swap; if registry persistence fails, the manager requests restoration using the previous artifact.

## Production integration
The included in-memory state store is for tests/development only. Production must provide a durable transactional registry, verified package artifact references, a sandboxed runtime, publisher trust roots, permission authorization and append-only audit. Extension code must not run inside the control-plane process or inherit ambient host credentials.
