from __future__ import annotations
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from agent_platform.api.digital_employees import create_digital_employee_router, create_team_template_router
from agent_platform.identity.models import Principal

class CatalogService:
    def __init__(self):
        self.installs = []
        self.employee = {"employee_id":"sales-analyst","name":"销售分析师","role":"sales",
            "status":"published","permission_declarations":["crm.read","report.write"]}
        self.template = {"template_id":"revenue-team","status":"published",
            "members":[{"employee_id":"sales-analyst","role":"analyst"}]}
    async def search_employees(self, **kwargs):
        return {"items":[self.employee],"page":kwargs["page"],"page_size":kwargs["page_size"]}
    async def get_employee(self, **kwargs):
        return self.employee if kwargs["employee_id"]=="sales-analyst" else None
    async def install_employee(self, **kwargs):
        self.installs.append(kwargs); return {"installation_id":"install-1",**kwargs}
    async def search_team_templates(self, **kwargs):
        return {"items":[self.template],"page":kwargs["page"]}
    async def get_team_template(self, **kwargs):
        return self.template if kwargs["template_id"]=="revenue-team" else None
    async def install_team_template(self, **kwargs):
        self.installs.append(kwargs); return {"team_id":"team-1",**kwargs}

class DigitalEmployeeApiTests(unittest.TestCase):
    def setUp(self):
        self.principal=Principal(user_id="user-a",tenant_id="tenant-a",session_id="session-a",email="user@example.test",display_name="User")
        self.service=CatalogService(); self.app=FastAPI()
        common=dict(resolve_principal=lambda:self.principal,
            resolve_service=lambda principal:self.service,
            authorize_read=lambda principal: principal.tenant_id=="tenant-a",
            authorize_install=lambda principal,scope: principal.tenant_id=="tenant-a")
        self.app.include_router(create_digital_employee_router(**common))
        self.app.include_router(create_team_template_router(**common))
        self.client=TestClient(self.app)
    def test_role_goal_discovery_is_tenant_scoped(self):
        r=self.client.get("/api/platform/v1/digital-employees?role=sales&goal=forecast")
        self.assertEqual(r.status_code,200); self.assertEqual(r.json()["items"][0]["employee_id"],"sales-analyst")
    def test_read_authorizer_false_is_forbidden(self):
        app = FastAPI()
        app.include_router(create_digital_employee_router(
            resolve_principal=lambda: self.principal,
            resolve_service=lambda principal: self.service,
            authorize_read=lambda principal: False,
            authorize_install=lambda principal, scope: True,
        ))
        response = TestClient(app).get("/api/platform/v1/digital-employees")
        self.assertEqual(response.status_code, 403)

    def test_install_rejects_permissions_outside_declared_boundary(self):
        r=self.client.post("/api/platform/v1/digital-employees/sales-analyst/install",
            json={"scope_id":"enterprise-a","owner_id":"owner-a","agent_version":"agent@1",
                  "permissions":["crm.delete"]})
        self.assertEqual(r.status_code,422); self.assertEqual(self.service.installs,[])
    def test_install_records_tenant_actor_and_permissions(self):
        r=self.client.post("/api/platform/v1/digital-employees/sales-analyst/install",
            json={"scope_id":"enterprise-a","owner_id":"owner-a","agent_version":"agent@1",
                  "permissions":["crm.read"]})
        self.assertEqual(r.status_code,201); self.assertEqual(self.service.installs[0]["tenant_id"],"tenant-a")
        self.assertEqual(self.service.installs[0]["permissions"],["crm.read"])
        self.assertEqual(self.service.installs[0]["actor_id"],"user-a")
    def test_team_template_rejects_unknown_member(self):
        r=self.client.post("/api/platform/v1/team-templates/revenue-team/install",
            json={"name":"Revenue","scope_id":"enterprise-a","owner_id":"owner-a",
                  "permissions_by_member":{"unknown":["crm.read"]}})
        self.assertEqual(r.status_code,422)
    def test_team_template_checks_each_members_declared_permissions(self):
        r=self.client.post("/api/platform/v1/team-templates/revenue-team/install",
            json={"name":"Revenue","scope_id":"enterprise-a","owner_id":"owner-a",
                  "permissions_by_member":{"sales-analyst":["crm.read"]}})
        self.assertEqual(r.status_code,201); self.assertEqual(
            self.service.installs[0]["permissions_by_member"]["sales-analyst"],["crm.read"])
