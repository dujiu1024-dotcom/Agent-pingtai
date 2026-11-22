import { expect, test } from '@playwright/test'

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

test('dashboard shell', async ({ page }) => {
  await stubBootstrap(page)
  await page.goto('/app/')
  await expect(page.locator('h2')).toContainText('智能体工作台')
  await expect(page.locator('nav')).toBeVisible()
})

test('deep link refresh', async ({ page }) => {
  await stubBootstrap(page)
  await page.goto('/app/agents/e2e-agent-01')
  await expect(page.locator('h1')).toContainText('智能体详情')
  await page.reload()
  await expect(page.locator('h1')).toContainText('智能体详情')
})

test('navigation toggle', async ({ page }) => {
  await stubBootstrap(page)
  await page.goto('/app/')
  await page.getByRole('button', { name: '折叠导航' }).click()
  await expect(page.getByRole('button', { name: '展开导航' })).toBeVisible()
  await page.getByRole('button', { name: '展开导航' }).click()
  await expect(page.getByRole('button', { name: '折叠导航' })).toBeVisible()
})

test('error states', async ({ page }) => {
  await stubBootstrap(page)
  await page.goto('/app/403')
  await expect(page.locator('.state-code')).toHaveText('403')
  await page.goto('/app/missing-route')
  await expect(page.locator('.state-code')).toHaveText('404')
})

test('responsive width', async ({ page }) => {
  await stubBootstrap(page)
  await page.goto('/app/')
  const width = page.viewportSize()?.width ?? 0
  const documentWidth = await page.evaluate(
    () => document.documentElement.scrollWidth,
  )
  expect(documentWidth).toBeLessThanOrEqual(width)
})
