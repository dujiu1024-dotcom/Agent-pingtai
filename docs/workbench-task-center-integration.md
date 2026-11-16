# Workbench task-center integration contract

The workbench does not instantiate a second agent runtime. Task submission must be delegated to the host execution service that already enforces Agent version resolution, Run lifecycle, Gateway-only tools, quotas, approvals, and checkpointing.

## Composition

Pass two adapters to `create_platform_app`:

- `task_service_resolver(principal)`: returns a tenant-bound object implementing `TaskCenterService`.
- `task_authorizer(principal, resource_id, action)`: authorizes the requested task/Agent/project/session operation.

When both are present, `task_execution=true` is published and the following routes are mounted:

| Method | Route | Service method |
|---|---|---|
| GET | `/api/platform/v1/workbench/tasks` | `list_tasks` |
| POST | `/api/platform/v1/workbench/tasks` | `submit_task` |
| GET | `/api/platform/v1/workbench/tasks/{task_id}` | `get_task` |
| POST | `/api/platform/v1/workbench/tasks/{task_id}/cancel` | `cancel_task` |

The resolver must return a tenant-bound service. If it exposes `repository.tenant_id`, the API verifies that it matches the authenticated principal.

## Required authorization actions

The API invokes the authorizer with these resource/action pairs:

- `("*", "read")`: list the caller-visible tasks.
- `("*", "create")`: permission to submit a task.
- `(agent_id, "execute")`: permission to execute the selected Agent.
- `(project_id, "use_project")`: permission to attach a project, when supplied.
- `(session_id, "use_session")`: permission to attach a session, when supplied.
- `(task_id, "read")`: view task details.
- `(task_id, "cancel")`: cancel a task.

The service must still enforce tenant and actor visibility when listing and retrieving tasks; API authorization is not a substitute for repository-level isolation.

## Service method requirements

`TaskCenterService` is declared in `agent_platform.api.workbench`. The host adapter must implement:

- `list_tasks(tenant_id, actor_id, limit)`: return only tasks visible to that actor.
- `submit_task(tenant_id, actor_id, agent_id, project_id, session_id, input, idempotency_key)`: resolve the active Agent version and submit through the existing runtime/Run dispatcher. Return a mapping containing at least `task_id`, `agent_id`, and `status`.
- `get_task(tenant_id, task_id)`: return the authorized task or `None`.
- `cancel_task(tenant_id, task_id, actor_id)`: cancel through the existing Run lifecycle; return the updated task or `None`.

Do not implement this adapter by directly invoking a model provider or by bypassing the approval/Gateway/Run lifecycle.

## Fail-closed behavior

If either adapter is absent, the workbench reports `task_execution=false` and does not expose task routes. This is intentional: the UI must not present a mock task queue as production execution.
