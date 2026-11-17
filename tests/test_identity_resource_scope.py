"""Cross-entrypoint tenant/resource scope enforcement tests."""
import unittest
from types import SimpleNamespace
from agent_platform.identity.models import Principal
from agent_platform.identity.resource_scope import ResourceScope, ResourceScopeAuthorizer
from agent_platform.identity.service import AuthorizationDenied

class FakeIdentity:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.calls = []
    async def authorize(self, *, principal, resource, permission_key):
        self.calls.append((principal, resource, permission_key))
        if not self.allowed:
            raise AuthorizationDenied("denied")

class ResourceScopeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.principal = Principal(user_id="user-a", tenant_id="tenant-a", session_id="s", email="a@example.com", display_name="A")
        self.identity = FakeIdentity()
        self.authorizer = ResourceScopeAuthorizer(identity=self.identity)

    async def test_rejects_cross_tenant_before_policy_lookup(self):
        with self.assertRaises(AuthorizationDenied):
            await self.authorizer.authorize(principal=self.principal, scope=ResourceScope("tenant-b", "file", "f-1", "file.read"))
        self.assertEqual(self.identity.calls, [])

    async def test_delegates_exact_resource_and_permission(self):
        await self.authorizer.authorize(principal=self.principal, scope=ResourceScope("tenant-a", "project", "p-1", "project.read"))
        _, resource, permission = self.identity.calls[0]
        self.assertEqual((resource.resource_type, resource.resource_id), ("project", "p-1"))
        self.assertEqual(permission, "project.read")

    async def test_gateway_adapter_binds_actor_tenant_and_server_resolved_resource(self):
        async def resolve(_context): return self.principal
        callback = self.authorizer.gateway_authorizer(resolve)
        context = SimpleNamespace(actor_id="user-a", tenant_id="tenant-a", resource="document:doc-1")
        self.assertTrue(await callback(context, {"document_id": "doc-1"}))
        self.assertEqual(self.identity.calls[-1][1].resource_id, "doc-1")
        context.tenant_id = "tenant-b"
        self.assertFalse(await callback(context, {}))

    async def test_harness_media_is_not_read_before_authorization(self):
        events = []
        async def resolve(_context): return self.principal
        async def read(_context, _media):
            events.append("read")
            return b"authorized"
        callback = self.authorizer.harness_media_resolver(resolve_principal=resolve, read_resource=read)
        context, media = SimpleNamespace(tenant_id="tenant-a"), SimpleNamespace(resource_id="file-1")
        self.assertEqual(await callback(context, media), b"authorized")
        self.assertEqual(events, ["read"])
        self.identity.allowed = False
        with self.assertRaises(AuthorizationDenied): await callback(context, media)
        self.assertEqual(events, ["read"])

    async def test_harness_output_is_not_written_before_authorization(self):
        events = []
        async def resolve(_context): return self.principal
        async def write(*args):
            events.append(args[1])
            return "artifact"
        callback = self.authorizer.harness_media_output_writer(resolve_principal=resolve, write_resource=write)
        context = SimpleNamespace(tenant_id="tenant-a")
        self.assertEqual(await callback(context, "out-1", "image", b"data", None), "artifact")
        self.identity.allowed = False
        with self.assertRaises(AuthorizationDenied): await callback(context, "out-2", "image", b"data", None)
        self.assertEqual(events, ["out-1"])
