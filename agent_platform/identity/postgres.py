"""PostgreSQL identity repository with tenant-scoped session and RBAC queries."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable
from uuid import uuid4

from agent_platform.identity.models import ApiCredentialRecord, InvitationRecord, SessionRecord, UserRecord

ConnectionFactory = Callable[[], Any]


class PostgresIdentityRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    @classmethod
    def from_dsn(cls, dsn: str) -> "PostgresIdentityRepository":
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        async def connect() -> Any:
            try:
                from psycopg import AsyncConnection
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("Install psycopg[binary,pool] for identity storage") from exc
            return await AsyncConnection.connect(dsn, row_factory=dict_row)
        return cls(connect)

    async def get_user_by_email(self, normalized_email: str) -> UserRecord | None:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute("SELECT user_id, email, display_name, password_hash, status FROM platform_users WHERE email_normalized=%s", (normalized_email,))
            row = await cursor.fetchone()
        if row is None:
            return None
        return UserRecord(user_id=str(row["user_id"]), email=row["email"], display_name=row["display_name"], password_hash=row["password_hash"], status=row["status"])

    async def is_active_member(self, *, tenant_id: str, user_id: str) -> bool:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """SELECT 1 FROM platform_tenant_memberships m
                   JOIN platform_tenants t
                     ON t.tenant_id=m.tenant_id AND t.status='active'
                   JOIN platform_users u ON u.user_id=m.user_id AND u.status='active'
                   WHERE m.tenant_id=%s AND m.user_id=%s AND m.status='active'""",
                (tenant_id, user_id),
            )
            return await cursor.fetchone() is not None

    async def create_session(self, *, session_id: str, tenant_id: str, user_id: str, token_hash: str, expires_at: datetime) -> None:
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute("""INSERT INTO platform_sessions (session_id, tenant_id, user_id, token_hash, expires_at)
                    SELECT %s, %s, %s, %s, %s WHERE EXISTS (
                      SELECT 1 FROM platform_tenant_memberships m
                    JOIN platform_tenants t ON t.tenant_id=m.tenant_id AND t.status='active'
                    JOIN platform_users u ON u.user_id=m.user_id AND u.status='active'
                    WHERE m.tenant_id=%s AND m.user_id=%s AND m.status='active'
                    ) RETURNING session_id""", (session_id, tenant_id, user_id, token_hash, expires_at, tenant_id, user_id))
                if await cursor.fetchone() is None:
                    raise PermissionError("active tenant membership required")
                await connection.execute(
                    "UPDATE platform_users SET last_login_at=now(), updated_at=now() WHERE user_id=%s",
                    (user_id,),
                )

    async def get_session_by_token_hash(self, token_hash: str) -> SessionRecord | None:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute("""SELECT s.session_id, s.tenant_id, s.user_id, s.expires_at, s.revoked_at,
                    u.email, u.display_name, u.status AS user_status, m.status AS membership_status
                FROM platform_sessions s JOIN platform_users u ON u.user_id=s.user_id
                JOIN platform_tenant_memberships m ON m.tenant_id=s.tenant_id AND m.user_id=s.user_id
                JOIN platform_tenants t ON t.tenant_id=s.tenant_id AND t.status='active'
                WHERE s.token_hash=%s""", (token_hash,))
            row = await cursor.fetchone()
        if row is None:
            return None
        return SessionRecord(session_id=str(row["session_id"]), tenant_id=row["tenant_id"], user_id=str(row["user_id"]), email=row["email"], display_name=row["display_name"], expires_at=row["expires_at"], revoked_at=row["revoked_at"], user_status=row["user_status"], membership_status=row["membership_status"])

    async def touch_session(self, *, session_id: str, tenant_id: str) -> bool:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """UPDATE platform_sessions SET last_seen_at=now()
                   WHERE session_id=%s AND tenant_id=%s
                     AND revoked_at IS NULL AND expires_at > now()
                     AND EXISTS (
                       SELECT 1 FROM platform_users u
                       JOIN platform_tenant_memberships m ON m.user_id=u.user_id
                       JOIN platform_tenants t ON t.tenant_id=m.tenant_id
                       WHERE u.user_id=platform_sessions.user_id
                         AND m.tenant_id=platform_sessions.tenant_id
                         AND u.status='active' AND m.status='active'
                         AND t.status='active'
                     )
                   RETURNING session_id""",
                (session_id, tenant_id),
            )
            return await cursor.fetchone() is not None

    async def revoke_session(self, *, session_id: str, tenant_id: str, user_id: str, revoked_at: datetime) -> bool:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute("UPDATE platform_sessions SET revoked_at=%s WHERE session_id=%s AND tenant_id=%s AND user_id=%s AND revoked_at IS NULL RETURNING session_id", (revoked_at, session_id, tenant_id, user_id))
            return await cursor.fetchone() is not None

    async def has_permission(self, *, tenant_id: str, user_id: str, resource_type: str, resource_id: str, permission_key: str) -> bool:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute("""SELECT EXISTS (
                SELECT 1 FROM platform_role_bindings b
                JOIN platform_role_permissions rp ON rp.tenant_id=b.tenant_id AND rp.role_id=b.role_id
                WHERE b.tenant_id=%s AND b.revoked_at IS NULL AND rp.permission_key=%s
                  AND EXISTS (
                    SELECT 1 FROM platform_tenant_memberships tm
                    WHERE tm.tenant_id=%s AND tm.user_id=%s AND tm.status='active'
                  )
                  AND EXISTS (
                    SELECT 1 FROM platform_tenants t
                    WHERE t.tenant_id=%s AND t.status='active'
                  )
                  AND EXISTS (
                    SELECT 1 FROM platform_users u
                    WHERE u.user_id=%s AND u.status='active'
                  )
                  AND ((b.subject_type='user' AND b.subject_id=%s)
                    OR (b.subject_type='team' AND b.subject_id IN (
                      SELECT tm.team_id FROM platform_team_memberships tm
                      JOIN platform_teams team
                        ON team.tenant_id=tm.tenant_id AND team.team_id=tm.team_id
                       AND team.status='active'
                      WHERE tm.tenant_id=%s AND tm.user_id=%s AND tm.status='active'
                    )))
                  AND (
                    (b.resource_type=%s AND b.resource_id=%s)
                    OR (b.resource_type='tenant' AND b.resource_id=%s)
                    OR (
                      %s='project' AND b.resource_type='organization'
                      AND b.resource_id = (
                        SELECT p.organization_id FROM platform_projects p
                        JOIN platform_organizations o
                          ON o.tenant_id=p.tenant_id
                         AND o.organization_id=p.organization_id
                         AND o.status='active'
                        WHERE p.tenant_id=%s AND p.project_id=%s
                      )
                    )
                  )
            ) AS allowed""", (
                tenant_id, permission_key, tenant_id, user_id, tenant_id, user_id,
                user_id, tenant_id, user_id, resource_type, resource_id, tenant_id, resource_type,
                tenant_id, resource_id,
            ))
            row = await cursor.fetchone()
            return bool(row["allowed"])


    async def _can_assign_role(
        self, connection: Any, *, tenant_id: str, actor_id: str, role_id: str,
    ) -> bool:
        cursor = await connection.execute(
            """SELECT
                 EXISTS (
                   SELECT 1 FROM platform_roles r
                   JOIN platform_tenants t
                     ON t.tenant_id=r.tenant_id AND t.status='active'
                   JOIN platform_tenant_memberships m
                     ON m.tenant_id=r.tenant_id AND m.user_id=%s
                    AND m.status='active'
                   JOIN platform_users u
                     ON u.user_id=m.user_id AND u.status='active'
                   WHERE r.tenant_id=%s AND r.role_id=%s
                 )
                 AND NOT EXISTS (
                   SELECT 1 FROM platform_role_permissions target
                   WHERE target.tenant_id=%s AND target.role_id=%s
                     AND NOT EXISTS (
                       SELECT 1 FROM platform_role_bindings b
                       JOIN platform_role_permissions held
                         ON held.tenant_id=b.tenant_id AND held.role_id=b.role_id
                       WHERE b.tenant_id=%s AND b.revoked_at IS NULL
                         AND b.resource_type='tenant' AND b.resource_id=%s
                         AND held.permission_key=target.permission_key
                         AND (
                           (b.subject_type='user' AND b.subject_id=%s)
                           OR (b.subject_type='team' AND b.subject_id IN (
                             SELECT tm.team_id FROM platform_team_memberships tm
                             JOIN platform_teams team
                               ON team.tenant_id=tm.tenant_id
                              AND team.team_id=tm.team_id AND team.status='active'
                             WHERE tm.tenant_id=%s AND tm.user_id=%s
                               AND tm.status='active'
                           ))
                         )
                     )
                 ) AS allowed""",
            (actor_id, tenant_id, role_id, tenant_id, role_id,
             tenant_id, tenant_id, actor_id, tenant_id, actor_id),
        )
        return bool((await cursor.fetchone())["allowed"])

    async def can_assign_role(
        self, *, tenant_id: str, actor_id: str, role_id: str,
    ) -> bool:
        connection = await self._connection_factory()
        async with connection:
            return await self._can_assign_role(
                connection, tenant_id=tenant_id, actor_id=actor_id, role_id=role_id,
            )

    async def grant_role(
        self, *, tenant_id: str, actor_id: str, subject_type: str,
        subject_id: str, resource_type: str, resource_id: str, role_id: str,
    ) -> str:
        binding_id = str(uuid4())
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                if not await self._can_assign_role(
                    connection, tenant_id=tenant_id, actor_id=actor_id, role_id=role_id,
                ):
                    raise PermissionError("cannot delegate permissions not held by actor")
                subject_exists = await connection.execute(
                    """SELECT CASE
                         WHEN %s='user' THEN EXISTS (
                           SELECT 1 FROM platform_tenant_memberships
                           WHERE tenant_id=%s AND user_id::text=%s AND status='active'
                         )
                         WHEN %s='team' THEN EXISTS (
                           SELECT 1 FROM platform_teams
                           WHERE tenant_id=%s AND team_id=%s AND status='active'
                         )
                         WHEN %s='service_account' THEN EXISTS (
                           SELECT 1 FROM platform_service_accounts
                           WHERE tenant_id=%s AND service_account_id=%s AND status='active'
                         )
                         WHEN %s='agent' THEN EXISTS (
                           SELECT 1 FROM platform_agent_identities
                           WHERE tenant_id=%s AND agent_id=%s AND status='active'
                         )
                         ELSE false END AS allowed""",
                    (subject_type, tenant_id, subject_id, subject_type, tenant_id,
                     subject_id, subject_type, tenant_id, subject_id,
                     subject_type, tenant_id, subject_id),
                )
                if not (await subject_exists.fetchone())["allowed"]:
                    raise PermissionError("role binding subject is not active in this tenant")
                await connection.execute(
                    """INSERT INTO platform_role_bindings
                       (binding_id, tenant_id, subject_type, subject_id,
                        resource_type, resource_id, role_id, created_by)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (binding_id, tenant_id, subject_type, subject_id,
                     resource_type, resource_id, role_id, actor_id),
                )
                await connection.execute(
                    """INSERT INTO platform_identity_audit
                       (tenant_id, actor_id, action, subject_id, outcome, detail)
                       VALUES (%s, %s, 'role.granted', %s, 'succeeded',
                         jsonb_build_object('binding_id', %s, 'role_id', %s,
                           'subject_type', %s, 'resource_type', %s, 'resource_id', %s))""",
                    (tenant_id, actor_id, subject_id, binding_id, role_id,
                     subject_type, resource_type, resource_id),
                )
        return binding_id

    async def revoke_role(
        self, *, tenant_id: str, actor_id: str, binding_id: str,
        resource_type: str, resource_id: str,
    ) -> bool:
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """UPDATE platform_role_bindings SET revoked_at=now()
                       WHERE tenant_id=%s AND binding_id=%s
                         AND resource_type=%s AND resource_id=%s
                         AND revoked_at IS NULL
                       RETURNING subject_id, role_id, resource_type, resource_id""",
                    (tenant_id, binding_id, resource_type, resource_id),
                )
                row = await cursor.fetchone()
                if row is None:
                    return False
                await connection.execute(
                    """INSERT INTO platform_identity_audit
                       (tenant_id, actor_id, action, subject_id, outcome, detail)
                       VALUES (%s, %s, 'role.revoked', %s, 'succeeded',
                         jsonb_build_object('binding_id', %s, 'role_id', %s,
                           'resource_type', %s, 'resource_id', %s))""",
                    (tenant_id, actor_id, row["subject_id"], binding_id,
                     row["role_id"], row["resource_type"], row["resource_id"]),
                )
        return True


    async def create_api_credential(
        self, *, credential_id: str, tenant_id: str, user_id: str, name: str,
        token_hash: str, scopes: tuple[str, ...], expires_at: datetime | None,
    ) -> None:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """INSERT INTO platform_api_credentials
                   (credential_id, tenant_id, user_id, name, token_hash, scopes, expires_at)
                   SELECT %s, %s, %s, %s, %s, %s, %s
                   WHERE EXISTS (
                     SELECT 1 FROM platform_tenant_memberships m
                     JOIN platform_tenants t ON t.tenant_id=m.tenant_id AND t.status='active'
                     JOIN platform_users u ON u.user_id=m.user_id AND u.status='active'
                     WHERE m.tenant_id=%s AND m.user_id=%s AND m.status='active'
                   ) RETURNING credential_id""",
                (credential_id, tenant_id, user_id, name, token_hash,
                 list(scopes), expires_at, tenant_id, user_id),
            )
            if await cursor.fetchone() is None:
                raise PermissionError("active tenant membership required")

    async def get_api_credential(self, token_hash: str) -> ApiCredentialRecord | None:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """SELECT c.credential_id, c.tenant_id, c.user_id, c.name,
                          c.scopes, c.expires_at, c.revoked_at,
                          u.status AS user_status, m.status AS membership_status
                   FROM platform_api_credentials c
                   JOIN platform_users u ON u.user_id=c.user_id
                   JOIN platform_tenant_memberships m
                     ON m.tenant_id=c.tenant_id AND m.user_id=c.user_id
                   JOIN platform_tenants t
                     ON t.tenant_id=c.tenant_id AND t.status='active'
                   WHERE c.token_hash=%s""",
                (token_hash,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return ApiCredentialRecord(
            credential_id=str(row["credential_id"]), tenant_id=row["tenant_id"],
            user_id=str(row["user_id"]), name=row["name"],
            scopes=tuple(row["scopes"]), expires_at=row["expires_at"],
            revoked_at=row["revoked_at"], user_status=row["user_status"],
            membership_status=row["membership_status"],
        )

    async def touch_api_credential(self, *, credential_id: str, tenant_id: str) -> bool:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """UPDATE platform_api_credentials c SET last_used_at=now()
                   WHERE c.credential_id=%s AND c.tenant_id=%s
                     AND c.revoked_at IS NULL
                     AND (c.expires_at IS NULL OR c.expires_at > now())
                     AND EXISTS (
                       SELECT 1 FROM platform_users u
                       JOIN platform_tenant_memberships m ON m.user_id=u.user_id
                       JOIN platform_tenants t ON t.tenant_id=m.tenant_id
                       WHERE u.user_id=c.user_id AND m.tenant_id=c.tenant_id
                         AND u.status='active' AND m.status='active'
                         AND t.status='active'
                     )
                   RETURNING c.credential_id""",
                (credential_id, tenant_id),
            )
            return await cursor.fetchone() is not None

    async def revoke_api_credential(
        self, *, credential_id: str, tenant_id: str, user_id: str,
    ) -> bool:
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """UPDATE platform_api_credentials
                       SET revoked_at=now()
                       WHERE credential_id=%s AND tenant_id=%s AND user_id=%s
                         AND revoked_at IS NULL
                       RETURNING name""",
                    (credential_id, tenant_id, user_id),
                )
                row = await cursor.fetchone()
                if row is None:
                    return False
                await connection.execute(
                    """INSERT INTO platform_identity_audit
                       (tenant_id, actor_id, action, subject_id, outcome, detail)
                       VALUES (%s, %s, 'api_credential.revoked', %s, 'succeeded',
                               jsonb_build_object('credential_id', %s, 'name', %s))""",
                    (tenant_id, user_id, credential_id, credential_id, row["name"]),
                )
        return True

    async def create_invitation(
        self, *, invitation_id: str, tenant_id: str, email_normalized: str,
        role_id: str, token_hash: str, invited_by: str, expires_at: datetime,
    ) -> None:
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """INSERT INTO platform_invitations
                       (invitation_id, tenant_id, email_normalized, role_id,
                        token_hash, invited_by, expires_at)
                       SELECT %s, %s, %s, %s, %s, %s, %s
                       WHERE EXISTS (
                         SELECT 1 FROM platform_tenants
                         WHERE tenant_id=%s AND status='active'
                       )
                       RETURNING invitation_id""",
                    (invitation_id, tenant_id, email_normalized, role_id,
                     token_hash, invited_by, expires_at, tenant_id),
                )
                if await cursor.fetchone() is None:
                    raise PermissionError("active tenant required")
                if not await self._can_assign_role(
                    connection, tenant_id=tenant_id, actor_id=invited_by, role_id=role_id,
                ):
                    raise PermissionError("cannot invite a user with permissions not held by actor")
                await connection.execute(
                    """INSERT INTO platform_identity_audit
                       (tenant_id, actor_id, action, subject_id, outcome, detail)
                       VALUES (%s, %s, 'invitation.created', %s, 'succeeded',
                         jsonb_build_object('email', %s, 'role_id', %s,
                           'invitation_id', %s))""",
                    (tenant_id, invited_by, email_normalized, email_normalized,
                     role_id, invitation_id),
                )

    async def get_invitation(self, token_hash: str) -> InvitationRecord | None:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """SELECT invitation_id, tenant_id, email_normalized, role_id,
                          expires_at, accepted_at
                   FROM platform_invitations
                   WHERE token_hash=%s""",
                (token_hash,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return InvitationRecord(
            invitation_id=str(row["invitation_id"]), tenant_id=row["tenant_id"],
            email_normalized=row["email_normalized"], role_id=row["role_id"],
            expires_at=row["expires_at"], accepted_at=row["accepted_at"],
        )

    async def accept_invitation(
        self, *, invitation_id: str, token_hash: str, tenant_id: str,
        user_id: str, email_normalized: str,
    ) -> bool:
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """SELECT i.email_normalized, i.role_id
                       FROM platform_invitations i
                       JOIN platform_tenants t
                         ON t.tenant_id=i.tenant_id AND t.status='active'
                       JOIN platform_users u
                         ON u.user_id=%s AND u.email_normalized=i.email_normalized
                        AND u.status='active'
                       WHERE i.invitation_id=%s AND i.token_hash=%s
                         AND i.tenant_id=%s AND i.email_normalized=%s
                         AND i.accepted_at IS NULL AND i.expires_at > now()
                       FOR UPDATE OF i""",
                    (user_id, invitation_id, token_hash, tenant_id, email_normalized),
                )
                invitation = await cursor.fetchone()
                if invitation is None:
                    return False
                cursor = await connection.execute(
                    """UPDATE platform_invitations SET accepted_at=now()
                       WHERE invitation_id=%s AND accepted_at IS NULL
                       RETURNING invitation_id""",
                    (invitation_id,),
                )
                if await cursor.fetchone() is None:
                    return False
                await connection.execute(
                    """INSERT INTO platform_tenant_memberships
                       (tenant_id, user_id, status, joined_at)
                       VALUES (%s, %s, 'active', now())
                       ON CONFLICT (tenant_id, user_id)
                       DO UPDATE SET status='active', joined_at=now()""",
                    (tenant_id, user_id),
                )
                await connection.execute(
                    """INSERT INTO platform_role_bindings
                       (binding_id, tenant_id, subject_type, subject_id,
                        resource_type, resource_id, role_id, created_by)
                       VALUES (%s, %s, 'user', %s, 'tenant', %s, %s, 'invitation')
                       ON CONFLICT DO NOTHING""",
                    (str(uuid4()), tenant_id, user_id, tenant_id, invitation["role_id"]),
                )
                await connection.execute(
                    """INSERT INTO platform_identity_audit
                       (tenant_id, actor_id, action, subject_id, outcome, detail)
                       VALUES (%s, %s, 'invitation.accepted', %s, 'succeeded',
                         jsonb_build_object('invitation_id', %s, 'role_id', %s))""",
                    (tenant_id, user_id, user_id, invitation_id, invitation["role_id"]),
                )
        return True
