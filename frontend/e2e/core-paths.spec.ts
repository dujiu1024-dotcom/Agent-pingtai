import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.route('**/api/platform/v1/bootstrap', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ auth: { enabled: false }, version: 'e2e', capabilities: [] }),
  }))
  await page.route('**/api/platform/v1/office/state', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      ok: true,
      data: {
        generated_at: '2026-11-19T10:00:00+08:00',
        metrics: { total: 0, open: 0, closed: 0, done: 0, agents: 0 },
        agents: [],
        llm: { running: [], model: 'test', tokens_today: 0 },
        tasks: [],
        runs: [],
        events: { pending: 0, list: [] },
      },
    }),
  }))
})

test('dashboard shell', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '欢迎回来！' })).toBeVisible()
  await expect(page.locator('.r3-primary-nav')).toBeVisible()
  await expect(page.getByRole('link', { name: '3D 办公室' })).toBeVisible()
})

test('deep link refresh', async ({ page }) => {
  await page.goto('/operations/agents/e2e-agent-01')
  await expect(page.locator('h1')).toContainText('Agent 运行详情')
  await page.reload()
  await expect(page.locator('h1')).toContainText('Agent 运行详情')
})

test('navigation toggle', async ({ page }) => {
  await page.goto('/app')
  await page.getByRole('button', { name: '折叠导航' }).click()
  await expect(page.getByRole('button', { name: '展开导航' })).toBeVisible()
  await page.getByRole('button', { name: '展开导航' }).click()
  await expect(page.getByRole('button', { name: '折叠导航' })).toBeVisible()
})

test('restricted and not-found states', async ({ page }) => {
  await page.goto('/admin/organization')
  await expect(page.locator('.state-code')).toHaveText('—')
  await page.goto('/missing-route')
  await expect(page.locator('.state-code')).toHaveText('404')
})

test('responsive width', async ({ page }) => {
  await page.goto('/app')
  const width = page.viewportSize()?.width ?? 0
  const documentWidth = await page.evaluate(
    () => document.documentElement.scrollWidth,
  )
  expect(documentWidth).toBeLessThanOrEqual(width)
})
