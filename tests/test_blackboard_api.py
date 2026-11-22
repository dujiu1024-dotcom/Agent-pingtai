from __future__ import annotations

import unittest
from dataclasses import replace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.blackboard import create_blackboard_router
from agent_platform.blackboard.protocol import VersionConflict
from agent_platform.contracts import BlackboardEntry, EntryKind, EntryStatus, Scope
from agent_platform.identity.models import Principal


class InMemoryBlackboard:
    def __init__(self, tenant_id="tenant-a"):
        self.tenant_id = tenant_id
        self.entries = {}
        self.history = {}

    async def propose(self, entry):
        if entry.entry_id in self.entries:
            raise RuntimeError("duplicate entry")
        self.entries[entry.entry_id] = entry
        self.history.setdefault(entry.entry_id, []).append(entry)
        return entry

    async def get(self, entry_id):
        return self.entries.get(entry_id)

    async def list_entries(self, *, scope, scope_id, kinds=(), limit=100):
        values = [
            entry for entry in self.entries.values()
            if entry.scope is scope and entry.scope_id == scope_id
            and (not kinds or entry.kind.value in kinds)
        ]
        return values[:limit]

    async def list_versions(self, entry_id):
        return list(self.history.get(entry_id, ()))

    async def revise(self, entry_id, *, expected_version, replacement):
        current = self.entries.get(entry_id)
        if current is None:
            raise KeyError(entry_id)
        if current.version != expected_version:
            raise VersionConflict("stale version")
        revised = replace(replacement, version=expected_version + 1)
        self.entries[entry_id] = revised
        self.history.setdefault(entry_id, []).append(revised)
        return revised

    async def record_challenge(self, entry_id, challenger_id, reason, evidence_refs):
        target = await self.get(entry_id)
        challenge = BlackboardEntry(
            scope=target.scope,
            scope_id=target.scope_id,
            kind=EntryKind.CHALLENGE,
            content={"target_entry_id": entry_id, "reason": reason},
            author_id=challenger_id,
            source_refs=tuple(evidence_refs),
        )
        return await self.propose(challenge)

    async def record_decision(self, conflict_id, decider_id, outcome, rationale, evidence_refs):
        conflict = await self.get(conflict_id)
        if conflict is None or conflict.kind is not EntryKind.CONFLICT:
            raise ValueError("not a conflict")
        decision = BlackboardEntry(
            scope=conflict.scope,
            scope_id=conflict.scope_id,
            kind=EntryKind.DECISION,
            content={"conflict_id": conflict_id, "outcome": outcome, "rationale": rationale},
            author_id=decider_id,
            source_refs=tuple(evidence_refs),
            status=EntryStatus.ACCEPTED,
        )
        return await self.propose(decision)


class BlackboardApiTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryBlackboard()
        self.evidence_allowed = True

        async def resolve_principal(request):
            return Principal(
                user_id=request.headers.get("x-user", "user-a"),
                tenant_id=request.headers.get("x-tenant", "tenant-a"),
                session_id="session-a",
                email="user@example.com",
                display_name="User",
            )

        async def resolve_repository(principal):
            return self.repository

        async def verify_evidence_refs(principal, scope, scope_id, refs):
            return self.evidence_allowed and all(
                ref.startswith(("source:", "claim:", "filing:")) for ref in refs
            )

        async def authorize_scope(principal, scope, scope_id, action):
            allowed_scopes = {
                (Scope.TASK, "task-a"),
                (Scope.PROJECT, "project-a"),
                (Scope.TENANT, "tenant-a"),
            }
            return (
                principal.tenant_id == self.repository.tenant_id
                and (scope, scope_id) in allowed_scopes
                and action in {"read", "write", "decide"}
            )

        app = FastAPI()
        app.include_router(create_blackboard_router(
            resolve_principal=resolve_principal,
            resolve_repository=resolve_repository,
            authorize_scope=authorize_scope,
            verify_evidence_refs=verify_evidence_refs,
        ))
        self.client = TestClient(app)

    def test_entry_history_returns_all_immutable_versions(self):
        proposed = self.client.post(
            "/api/platform/v1/blackboard/entries",
            json={
                "scope": "task", "scope_id": "task-a", "kind": "claim",
                "content": {"claim": "initial"},
            },
        )
        self.assertEqual(proposed.status_code, 201)
        entry_id = proposed.json()["entry_id"]
        revised = self.client.patch(
            f"/api/platform/v1/blackboard/entries/{entry_id}",
            json={"expected_version": 1, "content": {"claim": "corrected"}},
        )
        self.assertEqual(revised.status_code, 200)
        history = self.client.get(
            f"/api/platform/v1/blackboard/entries/{entry_id}/history",
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual([item["version"] for item in history.json()["items"]], [1, 2])
        self.assertEqual(history.json()["items"][0]["content"]["claim"], "initial")

    def test_propose_list_get_challenge_decide_and_revise(self):
        proposed = self.client.post(
            "/api/platform/v1/blackboard/entries",
            json={
                "scope": "task",
                "scope_id": "task-a",
                "kind": "conflict",
                "content": {"reason": "two experts disagree"},
                "source_refs": ["claim:1", "claim:2"],
            },
        )
        self.assertEqual(proposed.status_code, 201)
        conflict_id = proposed.json()["entry_id"]
        self.assertEqual(proposed.json()["author_id"], "user-a")

        listed = self.client.get(
            "/api/platform/v1/blackboard/entries",
            params={"scope": "task", "scope_id": "task-a", "kinds": "conflict"},
        )
        self.assertEqual(len(listed.json()["items"]), 1)
        self.assertEqual(
            self.client.get(f"/api/platform/v1/blackboard/entries/{conflict_id}").status_code,
            200,
        )

        challenge = self.client.post(
            f"/api/platform/v1/blackboard/entries/{conflict_id}/challenges",
            json={"reason": "source is stale", "evidence_refs": ["filing:2026"]},
        )
        self.assertEqual(challenge.status_code, 201)
        self.assertEqual(challenge.json()["kind"], "challenge")

        decision = self.client.post(
            f"/api/platform/v1/blackboard/entries/{conflict_id}/decisions",
            json={
                "outcome": "use audited filing",
                "rationale": "higher evidence authority",
                "evidence_refs": ["filing:2026"],
            },
        )
        self.assertEqual(decision.status_code, 201)
        self.assertEqual(decision.json()["status"], "accepted")

        revised = self.client.patch(
            f"/api/platform/v1/blackboard/entries/{conflict_id}",
            json={"expected_version": 1, "content": {"reason": "reconciled"}},
        )
        self.assertEqual(revised.status_code, 200)
        self.assertEqual(revised.json()["version"], 2)

        stale = self.client.patch(
            f"/api/platform/v1/blackboard/entries/{conflict_id}",
            json={"expected_version": 1, "content": {"reason": "stale update"}},
        )
        self.assertEqual(stale.status_code, 409)

    def test_evidence_references_must_be_verified_before_persisting(self):
        self.evidence_allowed = False
        denied = self.client.post(
            "/api/platform/v1/blackboard/entries",
            json={
                "scope": "task",
                "scope_id": "task-a",
                "kind": "fact",
                "content": {"fact": "unsupported"},
                "source_refs": ["source:missing"],
            },
        )
        self.assertEqual(denied.status_code, 422)
        self.assertEqual(self.repository.entries, {})

        self.evidence_allowed = True
        accepted = self.client.post(
            "/api/platform/v1/blackboard/entries",
            json={
                "scope": "task",
                "scope_id": "task-a",
                "kind": "fact",
                "content": {"fact": "supported"},
                "source_refs": ["source:verified"],
            },
        )
        self.assertEqual(accepted.status_code, 201)

    def test_evidence_verifier_is_required_when_references_are_present(self):
        async def resolve_principal(request):
            return Principal(
                user_id="user-a",
                tenant_id="tenant-a",
                session_id="session-a",
                email="user@example.com",
                display_name="User",
            )

        async def resolve_repository(principal):
            return self.repository

        async def authorize_scope(principal, scope, scope_id, action):
            return True

        app = FastAPI()
        app.include_router(create_blackboard_router(
            resolve_principal=resolve_principal,
            resolve_repository=resolve_repository,
            authorize_scope=authorize_scope,
        ))
        client = TestClient(app)
        response = client.post(
            "/api/platform/v1/blackboard/entries",
            json={
                "scope": "task",
                "scope_id": "task-a",
                "kind": "fact",
                "content": {"fact": "unverified"},
                "source_refs": ["source:unknown"],
            },
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.repository.entries, {})

    def test_scope_and_repository_tenant_are_enforced(self):
        denied = self.client.post(
            "/api/platform/v1/blackboard/entries",
            json={
                "scope": "task",
                "scope_id": "task-other",
                "kind": "fact",
                "content": {"fact": "not authorized"},
            },
        )
        self.assertEqual(denied.status_code, 403)

        wrong_tenant = self.client.get(
            "/api/platform/v1/blackboard/entries",
            params={"scope": "task", "scope_id": "task-a"},
            headers={"x-tenant": "tenant-b"},
        )
        self.assertEqual(wrong_tenant.status_code, 403)


    def test_project_and_tenant_scopes_are_authorized_independently(self):
        for scope, scope_id in (("project", "project-a"), ("tenant", "tenant-a")):
            response = self.client.post(
                "/api/platform/v1/blackboard/entries",
                json={
                    "scope": scope,
                    "scope_id": scope_id,
                    "kind": "fact",
                    "content": {"scope": scope},
                    "tenant_id": "tenant-b",
                    "author_id": "attacker",
                },
            )
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json()["author_id"], "user-a")
            self.assertEqual(response.json()["scope_id"], scope_id)

    def test_tenant_scope_cannot_target_another_tenant_even_if_policy_is_permissive(self):
        async def resolve_principal(request):
            return Principal(
                user_id="user-a",
                tenant_id="tenant-a",
                session_id="session-a",
                email="user@example.com",
                display_name="User",
            )

        async def resolve_repository(principal):
            return self.repository

        async def permissive_authorizer(principal, scope, scope_id, action):
            return True

        app = FastAPI()
        app.include_router(create_blackboard_router(
            resolve_principal=resolve_principal,
            resolve_repository=resolve_repository,
            authorize_scope=permissive_authorizer,
        ))
        client = TestClient(app)
        denied = client.post(
            "/api/platform/v1/blackboard/entries",
            json={
                "scope": "tenant",
                "scope_id": "tenant-b",
                "kind": "fact",
                "content": {"secret": "must not cross tenant boundary"},
            },
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.repository.entries, {})

    def test_stale_revision_conflict_preserves_current_version_and_history(self):
        proposed = self.client.post(
            "/api/platform/v1/blackboard/entries",
            json={
                "scope": "task",
                "scope_id": "task-a",
                "kind": "fact",
                "content": {"value": "initial"},
            },
        )
        self.assertEqual(proposed.status_code, 201)
        entry_id = proposed.json()["entry_id"]

        first_revision = self.client.patch(
            f"/api/platform/v1/blackboard/entries/{entry_id}",
            json={"expected_version": 1, "content": {"value": "winner"}},
        )
        self.assertEqual(first_revision.status_code, 200)
        self.assertEqual(first_revision.json()["version"], 2)

        stale_revision = self.client.patch(
            f"/api/platform/v1/blackboard/entries/{entry_id}",
            json={"expected_version": 1, "content": {"value": "stale loser"}},
        )
        self.assertEqual(stale_revision.status_code, 409)

        current = self.client.get(
            f"/api/platform/v1/blackboard/entries/{entry_id}",
        )
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.json()["version"], 2)
        self.assertEqual(current.json()["content"], {"value": "winner"})

        history = self.client.get(
            f"/api/platform/v1/blackboard/entries/{entry_id}/history",
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual(
            [(item["version"], item["content"]["value"]) for item in history.json()["items"]],
            [(1, "initial"), (2, "winner")],
        )

    def test_expected_version_rejects_boolean_and_non_positive_values(self):
        proposed = self.client.post(
            "/api/platform/v1/blackboard/entries",
            json={
                "scope": "task",
                "scope_id": "task-a",
                "kind": "fact",
                "content": {"value": "initial"},
            },
        )
        entry_id = proposed.json()["entry_id"]
        for invalid_version in (True, 0, -1):
            response = self.client.patch(
                f"/api/platform/v1/blackboard/entries/{entry_id}",
                json={"expected_version": invalid_version, "content": {"value": "invalid"}},
            )
            self.assertEqual(response.status_code, 400)

if __name__ == "__main__":
    unittest.main()
