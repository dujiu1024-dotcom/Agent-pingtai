"""Authenticated workbench capability and task-center API.

Task execution is delegated to the host's already-configured execution service;
this module does not create a second agent runtime or bypass Run/Gateway policy.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from agent_platform.identity.models import Principal

try:
    import asyncpg  # noqa: F401
except ImportError:  # pragma: no cover - optional dep
    asyncpg = None  # type: ignore[assignment]


class TaskCenterService(Protocol):
    async def list_tasks(
        self, *, tenant_id: str, actor_id: str, limit: int = 100,
    ) -> Sequence[Mapping[str, Any]]: ...

    async def submit_task(
        self, *, tenant_id: str, actor_id: str, agent_id: str,
        project_id: str | None, session_id: str | None,
        input: Mapping[str, Any], idempotency_key: str | None = None,
        priority: int = 100,
    ) -> Mapping[str, Any]: ...

    async def get_task(
        self, *, tenant_id: str, task_id: str, actor_id: str,
    ) -> Mapping[str, Any] | None: ...

    async def list_task_runs(
        self, *, tenant_id: str, task_id: str,
    ) -> Sequence[Mapping[str, Any]]: ...

    async def cancel_task(
        self, *, tenant_id: str, task_id: str, actor_id: str,
    ) -> Mapping[str, Any] | None: ...

    async def retry_task(
        self, *, tenant_id: str, task_id: str, actor_id: str,
        idempotency_key: str,
    ) -> Mapping[str, Any] | None: ...

    async def list_dead_letters(
        self, *, tenant_id: str, limit: int = 50, include_resolved: bool = False,
    ) -> Sequence[Mapping[str, Any]]: ...

    async def get_dead_letter(
        self, *, tenant_id: str, run_id: str,
    ) -> Mapping[str, Any] | None: ...

    async def retry_dead_letter(
        self, *, tenant_id: str, run_id: str, actor_id: str, idempotency_key: str,
    ) -> Mapping[str, Any] | None: ...

    async def resolve_dead_letter(
        self, *, tenant_id: str, run_id: str, actor_id: str,
    ) -> Mapping[str, Any] | None: ...


PrincipalResolver = Callable[[Request], Awaitable[Principal]]
TaskServiceResolver = Callable[[Principal], Awaitable[TaskCenterService]]
TaskAuthorizer = Callable[[Principal, str, str], Awaitable[bool]]


def create_workbench_router(
    *,
    features: Mapping[str, bool],
    resolve_principal: PrincipalResolver | None = None,
    resolve_task_service: TaskServiceResolver | None = None,
    authorize_task: TaskAuthorizer | None = None,
    connection_factory: Any = None,
    redis_client: Any = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/workbench", tags=["workbench"])
    snapshot = {str(key): bool(value) for key, value in features.items()}
    task_configured = all((
        resolve_principal is not None,
        resolve_task_service is not None,
        authorize_task is not None,
    ))
    if any(value is not None for value in (
        resolve_principal, resolve_task_service, authorize_task,
    )) and not task_configured:
        raise ValueError("task center principal, service resolver and authorizer must be configured together")
    if task_configured and not snapshot.get("task_execution", False):
        raise ValueError("task_execution capability must be enabled when task routes are mounted")

    @router.get("/capabilities")
    async def capabilities():
        return {"features": dict(snapshot)}

    if task_configured:
        async def task_context(request: Request, task_id: str, action: str):
            principal = await resolve_principal(request)
            if not await authorize_task(principal, task_id, action):
                raise HTTPException(403, "task access denied")
            service = await resolve_task_service(principal)
            bound_tenant = getattr(
                service, "tenant_id",
                getattr(getattr(service, "repository", None), "tenant_id", principal.tenant_id),
            )
            if bound_tenant != principal.tenant_id:
                raise HTTPException(403, "task service tenant mismatch")
            return principal, service

        @router.get("/tasks")
        async def list_tasks(request: Request, limit: int = 100):
            if not 1 <= limit <= 200:
                raise HTTPException(400, "limit must be between 1 and 200")
            principal, service = await task_context(request, "*", "read")
            try:
                items = await service.list_tasks(
                    tenant_id=principal.tenant_id, actor_id=principal.user_id,
                    limit=limit,
                )
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            return {"items": [dict(item) for item in items]}

        @router.post("/tasks", status_code=202)
        async def submit_task(request: Request):
            principal, service = await task_context(request, "*", "create")
            try:
                body = await request.json()
            except Exception as exc:
                raise HTTPException(400, "JSON object is required") from exc
            if not isinstance(body, dict):
                raise HTTPException(400, "JSON object is required")
            agent_id = str(body.get("agent_id") or "").strip()
            task_input = body.get("input")
            if not agent_id or not isinstance(task_input, dict) or not task_input:
                raise HTTPException(400, "agent_id and non-empty input object are required")
            project_id = str(body["project_id"]) if body.get("project_id") else None
            session_id = str(body["session_id"]) if body.get("session_id") else None
            if not await authorize_task(principal, agent_id, "execute"):
                raise HTTPException(403, "Agent execution access denied")
            if project_id and not await authorize_task(principal, project_id, "use_project"):
                raise HTTPException(403, "project access denied")
            if session_id and not await authorize_task(principal, session_id, "use_session"):
                raise HTTPException(403, "session access denied")
            try:
                priority = body.get("priority", 100)
                if type(priority) is not int or not 0 <= priority <= 1000:
                    raise HTTPException(422, "priority must be an integer between 0 and 1000")
                submit_kwargs: dict[str, Any] = {
                    "tenant_id": principal.tenant_id, "actor_id": principal.user_id,
                    "agent_id": agent_id, "project_id": project_id,
                    "session_id": session_id, "input": task_input,
                    "idempotency_key": (
                        str(body["idempotency_key"])
                        if body.get("idempotency_key") else None
                    ),
                    "priority": priority,
                }
                result = await service.submit_task(**submit_kwargs)
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            return dict(result)

        @router.get("/tasks/{task_id}")
        async def get_task(task_id: str, request: Request):
            principal, service = await task_context(request, task_id, "read")
            result = await service.get_task(
                tenant_id=principal.tenant_id, task_id=task_id,
                actor_id=principal.user_id,
            )
            if result is None:
                raise HTTPException(404, "task not found")
            return dict(result)

        @router.get("/tasks/{task_id}/runs")
        async def list_task_runs(task_id: str, request: Request):
            principal, service = await task_context(request, task_id, "read")
            # First confirm the task exists; returns 404 for missing tasks.
            task = await service.get_task(
                tenant_id=principal.tenant_id, task_id=task_id,
                actor_id=principal.user_id,
            )
            if task is None:
                raise HTTPException(404, "task not found")
            items = await service.list_task_runs(
                tenant_id=principal.tenant_id, task_id=task_id,
            )
            return {"items": [dict(item) for item in items]}

        @router.post("/tasks/{task_id}/retry")
        async def retry_task(task_id: str, request: Request):
            principal, service = await task_context(request, task_id, "retry")
            try:
                body = await request.json()
            except Exception as exc:
                raise HTTPException(400, "retry request body must be JSON") from exc
            idempotency_key = body.get("idempotency_key") if isinstance(body, Mapping) else None
            if not isinstance(idempotency_key, str) or not idempotency_key.strip():
                raise HTTPException(422, "idempotency_key is required for task retry")
            try:
                result = await service.retry_task(
                    tenant_id=principal.tenant_id, task_id=task_id,
                    actor_id=principal.user_id, idempotency_key=idempotency_key,
                )
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            if result is None:
                raise HTTPException(404, "task not found")
            return JSONResponse(dict(result), status_code=202)

        @router.get("/dead-letters")
        async def list_dead_letters(
            request: Request, limit: int = 50, include_resolved: bool = False,
        ):
            if not 1 <= limit <= 200:
                raise HTTPException(400, "limit must be between 1 and 200")
            principal, service = await task_context(request, "*", "read")
            if not hasattr(service, "list_dead_letters"):
                raise HTTPException(503, "dead letter management is not configured")
            try:
                items = await service.list_dead_letters(
                    tenant_id=principal.tenant_id, limit=limit,
                    include_resolved=include_resolved,
                )
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            return {"items": [dict(item) for item in items]}

        @router.post("/dead-letters/{run_id}/retry")
        async def retry_dead_letter(run_id: str, request: Request):
            principal = await resolve_principal(request)
            service = await resolve_task_service(principal)
            if not hasattr(service, "get_dead_letter"):
                raise HTTPException(503, "dead letter management is not configured")
            row = await service.get_dead_letter(
                tenant_id=principal.tenant_id, run_id=run_id,
            )
            if row is None:
                raise HTTPException(404, "dead letter not found")
            if row.get("resolved_at"):
                raise HTTPException(409, "dead letter already resolved")
            task_id = row.get("task_id")
            if not task_id:
                raise HTTPException(409, "dead letter is not linked to a task")
            if not await authorize_task(principal, str(task_id), "retry"):
                raise HTTPException(403, "task retry denied")
            try:
                body = await request.json()
            except Exception:
                body = {}
            key = body.get("idempotency_key") if isinstance(body, Mapping) else None
            if not isinstance(key, str) or not key.strip():
                key = f"dlq-retry:{run_id}:{uuid4().hex}"
            try:
                result = await service.retry_dead_letter(
                    tenant_id=principal.tenant_id, run_id=run_id,
                    actor_id=principal.user_id, idempotency_key=key,
                )
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            if result is None:
                raise HTTPException(404, "dead letter not found")
            return JSONResponse(dict(result), status_code=202)

        @router.post("/dead-letters/{run_id}/resolve")
        async def resolve_dead_letter(run_id: str, request: Request):
            principal = await resolve_principal(request)
            service = await resolve_task_service(principal)
            if not hasattr(service, "resolve_dead_letter"):
                raise HTTPException(503, "dead letter management is not configured")
            row = await service.get_dead_letter(
                tenant_id=principal.tenant_id, run_id=run_id,
            )
            if row is None:
                raise HTTPException(404, "dead letter not found")
            target = str(row.get("task_id") or "*")
            if not await authorize_task(principal, target, "retry"):
                raise HTTPException(403, "task retry denied")
            result = await service.resolve_dead_letter(
                tenant_id=principal.tenant_id, run_id=run_id,
                actor_id=principal.user_id,
            )
            if result is None:
                raise HTTPException(404, "dead letter not found")
            return result

        @router.post("/tasks/{task_id}/cancel")
        async def cancel_task(task_id: str, request: Request):
            principal, service = await task_context(request, task_id, "cancel")
            try:
                result = await service.cancel_task(
                    tenant_id=principal.tenant_id, task_id=task_id,
                    actor_id=principal.user_id,
                )
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            if result is None:
                raise HTTPException(404, "task not found")
            return dict(result)

    # ------------------------------------------------------------------
    # Ops / monitoring endpoints (optional — only registered when db pool provided)
    # ------------------------------------------------------------------
    async def _ops_conn():
        if connection_factory is None:
            return None
        try:
            return await connection_factory()
        except Exception:
            return None

    async def _fetch(conn, sql: str, *args):
        """Execute SQL and return all rows as list of dicts (psycopg compat)."""
        cur = await conn.execute(sql, args if args else None)
        return await cur.fetchall()

    async def _fetchrow(conn, sql: str, *args):
        """Execute SQL and return one row as dict (psycopg compat)."""
        cur = await conn.execute(sql, args if args else None)
        return await cur.fetchone()

    if connection_factory is not None:

        @router.get("/ops/worker-health")
        async def ops_worker_health(request: Request):
            # 1. Check in-process worker task status first
            inproc_worker_id: str | None = None
            inproc_running: bool = False
            inproc_last_poll: str | None = None
            app_state = getattr(request.app, "state", None)
            if app_state is not None:
                rx = getattr(app_state, "run_execution", None)
                if rx is not None:
                    task_worker = getattr(rx, "task_worker", None)
                    if task_worker is not None:
                        inproc_worker_id = getattr(task_worker, "worker_id", None)
                        stop_evt = getattr(task_worker, "_stop", None)
                        bg_task = getattr(task_worker, "_task", None)
                        inproc_running = (
                            stop_evt is not None and not stop_evt.is_set()
                            and bg_task is not None and not bg_task.done()
                        )
                        last_poll = getattr(task_worker, "_last_poll_at", None)
                        if last_poll is not None:
                            try:
                                inproc_last_poll = last_poll.isoformat()
                            except Exception:
                                pass

            # 2. Then check DB for historical worker activity
            conn = await _ops_conn()
            if conn is None:
                return {
                    "enabled": True,
                    "inproc_worker_id": inproc_worker_id,
                    "inproc_running": inproc_running,
                    "inproc_last_poll": inproc_last_poll,
                    "workers": [],
                    "active_online": 1 if inproc_running else 0,
                    "total": 0,
                    "note": "db unavailable; status from in-process task_worker only",
                }
            try:
                rows = await _fetch(conn, """
                    SELECT
                      worker_id,
                      COUNT(*) FILTER (WHERE lease_expires_at > now()) AS active_runs,
                      MAX(updated_at) AS last_seen,
                      MAX(lease_expires_at) AS last_lease
                    FROM platform_runs
                    WHERE worker_id IS NOT NULL
                    GROUP BY worker_id
                    ORDER BY last_seen DESC
                    LIMIT 50
                """)
            finally:
                try: await conn.close()  # noqa: E701
                except Exception: pass
            workers = []
            import datetime as _dt
            for r in rows:
                ws = dict(r)
                last_lease = ws.get("last_lease")
                secs_left = None
                if last_lease is not None:
                    try:
                        delta = last_lease - _dt.datetime.now(last_lease.tzinfo)
                        secs_left = max(0, int(delta.total_seconds()))
                    except Exception:
                        secs_left = None
                workers.append({
                    "worker_id": ws["worker_id"],
                    "active_runs": int(ws["active_runs"] or 0),
                    "last_seen": ws["last_seen"].isoformat() if ws.get("last_seen") else None,
                    "secs_left_on_lease": secs_left,
                })
            now_utc = _dt.datetime.now(_dt.timezone.utc)
            two_min_ago = now_utc - _dt.timedelta(minutes=2)
            online = []
            for w in workers:
                if w["secs_left_on_lease"] and w["secs_left_on_lease"] > 0:
                    online.append(w)
                    continue
                ls = w.get("last_seen")
                if ls:
                    try:
                        seen_dt = _dt.datetime.fromisoformat(ls)
                        if seen_dt.tzinfo is None:
                            seen_dt = seen_dt.replace(tzinfo=_dt.timezone.utc)
                        if seen_dt > two_min_ago:
                            online.append(w)
                    except Exception:
                        pass
            # If in-process worker_id is not in DB records, add it as a pseudo-entry
            db_worker_ids = {w["worker_id"] for w in workers}
            if inproc_worker_id and inproc_worker_id not in db_worker_ids:
                workers.append({
                    "worker_id": inproc_worker_id,
                    "active_runs": 0,
                    "last_seen": inproc_last_poll or now_utc.isoformat(),
                    "secs_left_on_lease": None,
                    "in_process": True,
                })

            # active_online = DB-confirmed (lease active / recent activity) + in-process running
            online_count = len(online)
            if inproc_running and not any(w.get("worker_id") == inproc_worker_id and w in online for w in workers):
                online_count += 1

            return {
                "enabled": True,
                "inproc_worker_id": inproc_worker_id,
                "inproc_running": inproc_running,
                "inproc_last_poll": inproc_last_poll,
                "workers": workers,
                "active_online": online_count,
                "total": len(workers),
            }

        @router.get("/ops/outbox-stats")
        async def ops_outbox_stats():
            conn = await _ops_conn()
            if conn is None:
                return {"enabled": False, "reason": "db connection unavailable", "topics": [], "total_pending": 0}
            try:
                by_topic = await _fetch(conn, """
                    SELECT
                      topic,
                      COUNT(*) FILTER (WHERE published_at IS NULL) AS pending,
                      COUNT(*) FILTER (WHERE last_error IS NOT NULL AND attempts >= 3) AS erroring,
                      COUNT(*) AS total
                    FROM platform_outbox
                    GROUP BY topic
                    ORDER BY pending DESC, total DESC
                """)
                totals = await _fetchrow(conn, """
                    SELECT
                      COUNT(*) FILTER (WHERE published_at IS NULL) AS pending,
                      COUNT(*) FILTER (WHERE lease_until < now() AND published_at IS NULL) AS lease_expired,
                      COUNT(*) AS total
                    FROM platform_outbox
                """)
            finally:
                try: await conn.close()  # noqa: E701
                except Exception: pass
            topics = [dict(r) for r in by_topic]
            for t in topics:
                t["pending"] = int(t.get("pending") or 0)
                t["erroring"] = int(t.get("erroring") or 0)
            return {
                "enabled": True,
                "topics": topics,
                "total_pending": int(totals["pending"] or 0),
                "lease_expired": int(totals["lease_expired"] or 0),
                "total": int(totals["total"] or 0),
            }

        @router.get("/ops/lease-stats")
        async def ops_lease_stats():
            conn = await _ops_conn()
            if conn is None:
                return {"enabled": False, "reason": "db connection unavailable", "active": 0, "expired": 0, "items": []}
            try:
                stats = await _fetchrow(conn, """
                    SELECT
                      COUNT(*) FILTER (WHERE lease_expires_at > now()) AS active,
                      COUNT(*) FILTER (WHERE lease_expires_at <= now() AND worker_id IS NOT NULL) AS expired,
                      COUNT(DISTINCT worker_id) FILTER (WHERE lease_expires_at > now()) AS active_workers,
                      COUNT(*) FILTER (WHERE worker_id IS NOT NULL) AS total_claimed
                    FROM platform_runs
                """)
                items = await _fetch(conn, """
                    SELECT run_id, worker_id, lease_expires_at, worker_attempts, status, updated_at
                    FROM platform_runs
                    WHERE worker_id IS NOT NULL
                      AND lease_expires_at > now()
                    ORDER BY lease_expires_at ASC
                    LIMIT 50
                """)
            finally:
                try: await conn.close()  # noqa: E701
                except Exception: pass
            import datetime as _dt
            now = _dt.datetime.now(_dt.timezone.utc)
            result_items = []
            for r in items:
                d = dict(r)
                lease = d.get("lease_expires_at")
                secs_left = None
                if lease is not None:
                    try:
                        secs_left = max(0, int((lease - now).total_seconds()))
                    except Exception:
                        secs_left = None
                result_items.append({
                    "run_id": str(d["run_id"]),
                    "worker_id": d["worker_id"],
                    "lease_expires_at": lease.isoformat() if lease else None,
                    "secs_left": secs_left,
                    "status": d.get("status"),
                    "attempts": int(d.get("worker_attempts") or 0),
                })
            return {
                "enabled": True,
                "active": int(stats["active"] or 0),
                "expired": int(stats["expired"] or 0),
                "active_workers": int(stats["active_workers"] or 0),
                "total_claimed": int(stats["total_claimed"] or 0),
                "items": result_items,
            }

        @router.get("/ops/streams-stats")
        async def ops_streams_stats():
            # Case 1: redis_client 根本没配置
            if redis_client is None:
                return {
                    "redis_ok": False,
                    "streams_active": False,
                    "deployment_note": "Redis 未配置，本地部署使用 Postgres Outbox 替代",
                    "streams_count": 0,
                    "consumers": [],
                    "pending_total": 0,
                }
            # Case 2: Redis 不可达
            try:
                await redis_client.ping()
            except Exception as exc:
                return {
                    "redis_ok": False,
                    "streams_active": False,
                    "reason": f"Redis 不可达: {exc}",
                    "streams_count": 0,
                    "consumers": [],
                    "pending_total": 0,
                }
            # Case 3: Redis 活着，probe stream keys + consumer groups
            try:
                keys = await redis_client.keys("platform*")
            except Exception:
                keys = []
            streams_list = sorted([
                k.decode() if isinstance(k, bytes) else str(k)
                for k in keys
            ])
            # 过滤出真正的 stream（排除 rate limiter hash 等非 stream key）
            stream_type_count = 0
            for k in keys:
                try:
                    t = await redis_client.type(k)
                    t_str = t.decode() if isinstance(t, bytes) else str(t)
                    if t_str == "stream":
                        stream_type_count += 1
                except Exception:
                    pass

            consumers = []
            pending_total = 0
            for key in sorted(keys):
                try:
                    groups = await redis_client.xinfo_groups(key)
                    for g in groups or []:
                        try:
                            pending = await redis_client.xpending(key, g.get("name") or b"")
                        except Exception:
                            pending = 0
                        consumers.append({
                            "stream": key.decode() if isinstance(key, bytes) else str(key),
                            "group": (g.get("name") or b"").decode() if isinstance(g.get("name"), bytes) else str(g.get("name", "")),
                            "pending": int(pending) if pending is not None else 0,
                            "last_delivered_id": str(g.get("last-delivered-id", "")),
                        })
                        pending_total += consumers[-1]["pending"]
                except Exception:
                    pass

            # Case 3a: 有 consumer groups → 常规模式
            # Case 3b: 没有 consumer groups 但有 stream activity → 本地发布模式（OutboxRelay 在 XADD）
            # Case 3c: 什么都没有 → 干净但没在用
            return {
                "redis_ok": True,
                "streams_active": len(consumers) > 0,
                "streams_count": stream_type_count,
                "streams_found": streams_list,
                "consumers": consumers,
                "pending_total": pending_total,
                "deployment_mode": (
                    "consumer-groups" if consumers else
                    "producer-only" if stream_type_count > 0 else
                    "idle"
                ),
                "deployment_note": (
                    None if consumers else
                    (f"Redis 可用，存在 {stream_type_count} 个 Stream key 但未启用消费者组。"
                     f"本地部署通常由 Outbox Relay XADD 发布消息，消费端使用独立 Worker。")
                    if stream_type_count > 0 else
                    "Redis 可用但无 Stream 活动，当前未使用 Redis Streams 能力"
                ),
            }

    return router
