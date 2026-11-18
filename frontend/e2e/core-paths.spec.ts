import { expect, test } from '@playwright/test'

test('dashboard shell', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('h2')).toContainText('智能体工作台')
  await expect(page.locator('nav')).toBeVisible()
})

test('deep link refresh', async ({ page }) => {
  await page.goto('/agents/e2e-agent-01')
  await expect(page.locator('h1')).toContainText('智能体详情')
  await page.reload()
  await expect(page.locator('h1')).toContainText('智能体详情')
})

test('navigation toggle', async ({ page }) => {
  await page.goto('/app')
  await page.getByRole('button', { name: '折叠导航' }).click()
  await expect(page.getByRole('button', { name: '展开导航' })).toBeVisible()
  await page.getByRole('button', { name: '展开导航' }).click()
  await expect(page.getByRole('button', { name: '折叠导航' })).toBeVisible()
})

test('error states', async ({ page }) => {
  await page.goto('/403')
  await expect(page.locator('.state-code')).toHaveText('403')
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
