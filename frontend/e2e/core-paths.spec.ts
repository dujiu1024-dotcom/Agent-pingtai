import { expect, test } from '@playwright/test'

test('opens the dashboard without an interactive login prerequisite', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '智能体工作台' })).toBeVisible()
  await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible()
  await expect(page.getByRole('button', { name: '全局搜索' })).toBeVisible()
})

test('supports a deep-linked entity route and browser refresh', async ({ page }) => {
  await page.goto('/agents/e2e-agent-01')
  await expect(page.getByRole('heading', { name: '智能体详情' })).toBeVisible()
  await expect(page.getByRole('navigation', { name: '面包屑导航' })).toContainText('智能体')
  await page.reload()
  await expect(page.getByRole('heading', { name: '智能体详情' })).toBeVisible()
})

test('collapses and expands the three-level navigation shell', async ({ page }) => {
  await page.goto('/app')
  await page.getByRole('button', { name: '折叠导航' }).click()
  await expect(page.getByRole('button', { name: '展开导航' })).toBeVisible()
  await page.getByRole('button', { name: '展开导航' }).click()
  await expect(page.getByRole('button', { name: '折叠导航' })).toBeVisible()
})

test('renders explicit forbidden and not-found states', async ({ page }) => {
  await page.goto('/403')
  await expect(page.getByText('403', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '无权访问' })).toBeVisible()

  await page.goto('/route-that-does-not-exist')
  await expect(page.getByText('404', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '页面不存在' })).toBeVisible()
})

test('keeps the dashboard usable without horizontal overflow at desktop and tablet widths', async ({ page }) => {
  await page.goto('/app')
  const viewport = page.viewportSize()
  expect(viewport).not.toBeNull()
  const width = viewport?.width ?? 0
  await expect(page.getByRole('heading', { name: '智能体工作台' })).toBeVisible()
  await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible()
  const documentWidth = await page.evaluate(() => document.documentElement.scrollWidth)
  expect(documentWidth).toBeLessThanOrEqual(width)
})
