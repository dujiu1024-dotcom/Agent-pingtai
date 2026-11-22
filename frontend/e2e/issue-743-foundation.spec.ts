import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const bootstrapUrl = '**/api/platform/v1/bootstrap'
const appPath = (path = '/') => `/app${path}`

async function configureMode(page: Page, authEnabled = false) {
  let bootstrapRequests = 0
  await page.route(bootstrapUrl, async (route) => {
    bootstrapRequests += 1
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        auth: { enabled: authEnabled },
        version: 'issue-743-e2e',
        capabilities: [],
      }),
    })
  })
  return () => bootstrapRequests
}

test.describe('Issue #743 frontend foundation acceptance', () => {
  test('root entry resolves to the canonical dashboard using server bootstrap', async ({
    page,
  }) => {
    const bootstrapCount = await configureMode(page)
    await page.goto(appPath())

    await expect(page).toHaveURL(/\/app\/workspace\/overview$/)
    await expect(
      page.getByRole('heading', { name: '运营驾驶舱' }),
    ).toBeVisible()
    await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible()
    await expect(page.getByRole('link', { name: '打开全局搜索' })).toBeVisible()
    await expect(page.getByRole('link', { name: '打开通知中心' })).toBeVisible()
    expect(bootstrapCount()).toBe(1)
  })

  test('a no-auth deep link survives direct navigation and does not call identity APIs', async ({
    page,
  }) => {
    await configureMode(page, false)
    const identityRequests: string[] = []
    await page.route('**/api/**', async (route) => {
      const pathname = new URL(route.request().url()).pathname
      if (/\/(auth|identity|session)(\/|$)/i.test(pathname)) {
        identityRequests.push(pathname)
        await route.fulfill({ status: 404, body: '{}' })
        return
      }
      await route.continue()
    })

    await page.goto(appPath('/workspace/status'))
    await expect(page).toHaveURL(/\/app\/workspace\/status$/)
    await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible()
    await expect(page.getByText('免登录模式')).toBeVisible()
    expect(identityRequests).toEqual([])

    await page.goto(appPath('/auth/login'))
    await expect(page).toHaveURL(/\/app\/workspace\/overview$/)
    expect(identityRequests).toEqual([])
  })

  test('an auth-enabled bootstrap keeps identity entry conditional on server mode', async ({
    page,
  }) => {
    await configureMode(page, true)
    await page.goto(appPath('/'))

    await expect(page).toHaveURL(/\/app\/workspace\/overview$/)
    await expect(page.getByText('权限已启用')).toBeVisible()
    await expect(page.getByRole('link', { name: /登录/ })).toBeVisible()
  })

  for (const viewport of [
    { name: 'desktop', width: 1440, height: 900 },
    { name: 'tablet', width: 768, height: 1024 },
  ]) {
    test(`keeps the navigation and primary actions usable at ${viewport.name} width`, async ({
      page,
    }) => {
      await page.setViewportSize({
        width: viewport.width,
        height: viewport.height,
      })
      await configureMode(page)
      await page.goto(appPath('/workspace/overview'))

      await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible()
      await expect(page.getByRole('link', { name: '打开全局搜索' })).toBeVisible()
      await expect(page.getByRole('link', { name: '打开通知中心' })).toBeVisible()
      const dimensions = await page.evaluate(() => ({
        viewport: document.documentElement.clientWidth,
        content: document.documentElement.scrollWidth,
      }))
      expect(dimensions.content).toBeLessThanOrEqual(dimensions.viewport)
    })
  }
})
