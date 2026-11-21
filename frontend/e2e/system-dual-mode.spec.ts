import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

import {
  authRoutes,
  detailRoutes,
  globalRoutes,
  pageRoutes,
} from '../src/routes/route-config'

const bootstrapUrl = '**/api/platform/v1/bootstrap'
const businessRoutes = [...pageRoutes, ...detailRoutes, ...globalRoutes]

async function stubBootstrap(
  page: Page,
  authEnabled: boolean,
) {
  await page.route(bootstrapUrl, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        auth: { enabled: authEnabled },
        version: 'e2e',
        capabilities: ['prompt_management'],
      }),
    })
  })
}

function concretePath(path: string): string {
  return path.replace(/:([A-Za-z][A-Za-z0-9_]*)/g, '$1-e2e')
}

async function stubSession(page: Page) {
  await page.route('**/auth/me', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_id: 'acme',
        principal_type: 'user',
        user: {
          user_id: 'u-1',
          email: 'owner@acme.test',
          display_name: 'Owner',
        },
      }),
    })
  })
}

test.describe('Issue #752 dual-mode full-site browser smoke', () => {
  test('auth=false: all business routes load and identity entry points stay closed', async ({
    page,
  }) => {
    await stubBootstrap(page, false)

    for (const route of businessRoutes) {
      await page.goto(concretePath(route.path))
      await expect(
        page.getByRole('heading', { level: 1, name: route.title }),
        `route should load in auth=false: ${route.path}`,
      ).toBeVisible()
    }

    await page.goto('/workspace/overview')
    await expect(page.getByText('免登录模式')).toBeVisible()
    await expect(
      page.getByRole('link', { name: '登录', exact: true }),
    ).toHaveCount(0)

    for (const route of authRoutes) {
      await page.goto(route.path)
      await expect(
        page.getByRole('heading', { level: 1, name: '运营驾驶舱' }),
        `identity route should redirect in auth=false: ${route.path}`,
      ).toBeVisible()
    }
  })

  test('auth=true: business routes require a session and identity routes are exposed', async ({
    page,
  }) => {
    await stubBootstrap(page, true)
    await page.route('**/auth/me', async (route) => {
      await route.fulfill({ status: 401, body: '{}' })
    })

    await page.goto('/auth/login')
    await expect(
      page.getByRole('heading', { level: 1, name: '登录 Agent 平台' }),
    ).toBeVisible()
    await page.goto('/auth/initialize')
    await expect(
      page.getByRole('heading', { level: 1, name: '初始化平台管理员' }),
    ).toBeVisible()

    await page.unroute('**/auth/me')
    await stubSession(page)

    for (const route of businessRoutes) {
      await page.goto(concretePath(route.path))
      await expect(
        page.getByRole('heading', { level: 1, name: route.title }),
        `route should load in auth=true: ${route.path}`,
      ).toBeVisible()
    }

    await page.goto('/workspace/overview')
    await expect(page.getByText('权限模式')).toBeVisible()
    await expect(page.getByText('Owner')).toBeVisible()
  })

  test('deep links survive a hard refresh in both modes', async ({ page }) => {
    for (const authEnabled of [false, true]) {
      await stubBootstrap(page, authEnabled)
      if (authEnabled) await stubSession(page)
      await page.goto('/operations/runs/run-752-e2e')
      await expect(
        page.getByRole('heading', { level: 1, name: 'Run Trace 详情' }),
      ).toBeVisible()
      await page.reload()
      await expect(
        page.getByRole('heading', { level: 1, name: 'Run Trace 详情' }),
      ).toBeVisible()
    }
  })

  test('bootstrap failure is explicit and does not guess an auth mode', async ({
    page,
  }) => {
    await page.route(bootstrapUrl, async (route) => {
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'bootstrap unavailable' }),
      })
    })
    await page.goto('/workspace/overview')
    await expect(
      page.getByRole('heading', { name: '无法读取服务端启动配置' }),
    ).toBeVisible()
    await expect(page.getByText(/不会自行选择权限模式/)).toBeVisible()
  })
})
