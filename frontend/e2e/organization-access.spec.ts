import { expect, test } from '@playwright/test'

const organizationPayload = {
  items: [
    {
      section: 'tenants',
      rows: [{ tenant_id: 'acme', display_name: 'Acme', status: 'active' }],
      count: 1,
    },
    {
      section: 'users',
      rows: [{ user_id: 'u-1', email: 'owner@acme.test', status: 'active' }],
      count: 1,
    },
    {
      section: 'tenant_memberships',
      rows: [{ tenant_id: 'acme', user_id: 'u-1', status: 'active' }],
      count: 1,
    },
  ],
}

const accessPayload = {
  items: [
    {
      section: 'roles',
      rows: [{ role_id: 'owner', name: '所有者', is_system: true }],
      count: 1,
    },
    {
      section: 'role_bindings',
      rows: [{ role_id: 'owner', subject_type: 'user', subject_id: 'u-1' }],
      count: 1,
    },
    {
      section: 'role_permissions',
      rows: [{ role_id: 'owner', permission_key: 'platform.admin' }],
      count: 1,
    },
  ],
}

async function stubBootstrap(page: import('@playwright/test').Page) {
  await page.route('**/api/platform/v1/bootstrap', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        auth: { enabled: false },
        version: 'e2e',
        capabilities: [],
      }),
    })
  })
}

test('organization route renders live admin data', async ({ page }) => {
  await stubBootstrap(page)
  await page.route('**/api/platform/v1/admin/organization', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(organizationPayload),
    })
  })
  await page.goto('/app/admin/organization')
  await expect(page.getByRole('heading', { name: '组织与成员' })).toBeVisible()
  await expect(page.getByText('owner@acme.test')).toBeVisible()
  await expect(page.locator('.admin-section')).toHaveCount(3)
})

test('RBAC route renders live access-control data', async ({ page }) => {
  await stubBootstrap(page)
  await page.route('**/api/platform/v1/admin/access-control', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(accessPayload),
    })
  })
  await page.goto('/app/admin/access-control')
  await expect(page.getByRole('heading', { name: '角色与权限' })).toBeVisible()
  await expect(page.getByText('platform.admin')).toBeVisible()
  await expect(page.locator('.admin-section')).toHaveCount(3)
})
