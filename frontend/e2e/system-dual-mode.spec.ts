import { expect, test } from '@playwright/test'

import {
  authRoutes,
  detailRoutes,
  globalRoutes,
  pageRoutes,
} from '../src/routes/route-config'

const bootstrapUrl = '**/api/platform/v1/bootstrap'
const businessRoutes = [...pageRoutes, ...detailRoutes, ...globalRoutes]

async function stubBootstrap(
  page: Parameters<Parameters<typeof test>[1]>[0]['page'],
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
        page.getByText('当前模式不提供身份入口'),
        `identity route should be restricted in auth=false: ${route.path}`,
      ).toBeVisible()
    }
  })

  test('auth=true: business routes remain available and identity routes are exposed', async ({
    page,
  }) => {
    await stubBootstrap(page, true)

    for (const route of businessRoutes) {
      await page.goto(concretePath(route.path))
      await expect(
        page.getByRole('heading', { level: 1, name: route.title }),
        `route should load in auth=true: ${route.path}`,
      ).toBeVisible()
    }

    await page.goto('/workspace/overview')
    await expect(page.getByText('权限模式')).toBeVisible()
    await expect(
      page.getByRole('link', { name: '登录', exact: true }),
    ).toBeVisible()

    for (const route of authRoutes) {
      await page.goto(route.path)
      await expect(
        page.getByRole('heading', { level: 1, name: route.title }),
      ).toBeVisible()
    }
  })

  test('deep links survive a hard refresh in both modes', async ({ page }) => {
    for (const authEnabled of [false, true]) {
      await stubBootstrap(page, authEnabled)
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
