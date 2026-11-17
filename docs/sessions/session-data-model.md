# Session / Conversation Data Model (C-25-01)

## Canonical records

- `ConversationSession` identifies one tenant-owned conversation, its owner,
  optional agent/project binding, lifecycle status and timestamps.
- `SessionIsolationScope` distinguishes `user`, `agent`, `project` and
  explicitly `tenant`-scoped sessions. Tenant ID remains a mandatory outer
  partition for every scope. Agent and project scopes require their respective
  identifiers; selecting a scope does not itself grant access. The API's
  authenticated Principal and resource/project authorizer remain authoritative.
- `ConversationMessage` stores one chronological message with role, actor,
  content, run/task linkage, asset references, metadata and optional typed
  `MessageTokenUsage`. Token counts are non-negative and total tokens must
  equal prompt plus completion tokens.
- `ContextWindowSnapshot` is a versioned immutable value bound to a tenant and
  session. It stores a JSON context payload, run/message cursor, token count,
  configured maximum and creator. Snapshots exceeding their token budget are
  rejected.

## PostgreSQL schema

Migration `082_session_conversation_context.sql` extends the existing
`platform_conversation_sessions` and `platform_messages` records and creates
`platform_context_window_snapshots`. Composite tenant/session foreign keys,
tenant-scoped primary/unique keys, scope constraints and session/run indexes
prevent cross-tenant references and support later checkpoint retrieval.

## Isolation and memory boundary

Session scope is not an ACL grant. Every read/write still requires authenticated
tenant identity and resource authorization; project-bound sessions additionally
require project authorization. User sessions are owner-scoped; agent/project
sessions carry explicit scope identifiers; tenant-wide scope must be selected
explicitly and remains subject to tenant policy.

Session messages and context snapshots are the canonical short-lived transcript
and execution context. They are not copied into long-term memory. Memory may
retain only distilled facts after the existing extraction/review flow and keeps
a version-pinned `session://tenant/record@version` source reference. Session
history remains the source of truth.

This increment defines models and schema only. Message append/checkpoint/restart
orchestration, session management APIs, retention/deletion propagation and
production worker integration belong to subsequent C-25 tasks.
