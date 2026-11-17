# C-15-04 Sandbox credential boundary

The OCI runner is a deny-by-default execution boundary for untrusted Agent and
extension code. This note records the credential-specific hardening layered on
the existing filesystem, namespace, network, and resource controls.

## Host boundary

- The runner invokes a digest-pinned image with `--network=none`, a read-only
  root filesystem, non-root UID, dropped Linux capabilities, and
  `no-new-privileges`.
- Writable paths are bounded tmpfs mounts at `/tmp` and `/workspace`; no
  host bind mounts, devices, host namespaces, runtime socket, or host paths
  are passed into the container.
- Podman uses private user and PID namespaces. Docker configuration is rejected
  unless deployment configuration explicitly attests that rootless mode or
  daemon user-namespace remapping has been verified.
- CPU, memory, PID count, tmpfs, file size, open files, stdin, output, and
  execution time are bounded by the sandbox configuration.

## Credential boundary

- The container receives no inherited host environment. The runner only emits
  keys explicitly allowlisted by trusted server configuration.
- Allowlist validation rejects credential-bearing names, including access
  keys, private keys, tokens, passwords, credentials, authorization/bearer
  values, cookies/sessions, certificates, SSH sockets, and Docker endpoints.
- Environment values are validated as bounded NUL-free strings. The sandbox
  receives input over stdin, not host-mounted files.
- A caller cannot use an unapproved key to pass host credentials or runtime
  socket locations into the workload.

## Failure behavior and validation

Missing OCI runtime, invalid image/configuration, unknown environment keys,
oversized input, and timeout violations fail closed. Unit tests assert the
credential-name denylist and existing network/filesystem/namespace/resource
flags. Live daemon isolation, host filesystem/network probes, and container
escape testing remain deployment acceptance work and are intentionally not
performed in this task.


## A-15-02 acceptance evidence

The host-environment allowlist additionally rejects database endpoint/DSN variables (`DATABASE_URL`, `DB_*`, `PG*`, `POSTGRES*`, `MYSQL*`, `REDIS*`, `SQLITE*`, JDBC/connection/URI forms). This prevents a trusted but overly broad environment allowlist from accidentally forwarding platform database coordinates or credentials. Unit coverage checks these names are rejected and that the generated OCI argv contains no host network, host namespaces, privileged mode, bind mounts, devices, env-file forwarding, runtime socket, or unapproved environment injection.
