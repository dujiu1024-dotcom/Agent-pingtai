import { expect, test } from '@playwright/test'

async function stubBootstrap(page: import('@playwright/test').Page) {
  await page.route('**/api/platform/v1/bootstrap', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        auth: { enabled: false },
        version: 'issue-818-e2e',
        capabilities: [],
      }),
    })
  })
}

test.describe('Issue #818 navigation, top bar and route guard', () => {
  test('renders six primary navigation entries and keeps active state in sync with routes', async ({
    page,
  }) => {
    await stubBootstrap(page)
    await page.goto('/app/workspace/overview')

    const shell = page.locator('.app-shell')
    await expect(shell).toHaveAttribute('data-v', 'home')

    const navigation = page.locator('.primary-navigation')
    for (const label of [
      '工作台',
      '3D 办公室',
      '任务中心',
      'Agent 协作',
      '工作流与系统管理',
      '成本分摊',
    ]) {
      await expect(navigation.getByRole('link', { name: label })).toBeVisible()
    }
    await expect(navigation.locator('.nav-item')).toHaveCount(6)
    await expect(
      navigation.getByRole('link', { name: '工作台' }),
    ).toHaveAttribute('aria-current', 'page')

    await navigation.getByRole('link', { name: '3D 办公室' }).click()
    await expect(page).toHaveURL(/\/app\/workspace\/office$/)
    await expect(shell).toHaveAttribute('data-v', 'office')

    await navigation.getByRole('link', { name: '任务中心' }).click()
    await expect(page).toHaveURL(/\/app\/workspace\/tasks$/)
    await expect(shell).toHaveAttribute('data-v', 'home')
    await expect(
      navigation.getByRole('link', { name: '任务中心' }),
    ).toHaveAttribute('aria-current', 'page')
  })

  test('exposes top-bar search, notifications, avatar and collapsed-sidebar feedback', async ({
    page,
  }) => {
    await stubBootstrap(page)
    await page.goto('/app/workspace/overview')

    await expect(
      page.getByRole('link', { name: '打开全局搜索' }),
    ).toBeVisible()
    await expect(page.getByText('搜索 Agent、任务、文档…')).toBeVisible()
    await expect(
      page.getByRole('link', { name: '打开通知中心' }),
    ).toBeVisible()
    await expect(page.locator('.topbar .avatar')).toBeVisible()

    await page.getByRole('button', { name: '折叠导航' }).click()
    await expect(
      page.getByRole('status').filter({ hasText: '已收起侧边栏（演示）' }),
    ).toBeVisible()
    await page.getByRole('button', { name: '展开导航' }).click()
    await expect(
      page.getByText('已收起侧边栏（演示）'),
    ).toHaveCount(0)
  })

  test('redirects no-auth login and initialization routes to the workspace', async ({
    page,
  }) => {
    await stubBootstrap(page)

    await page.goto('/app/auth/login')
    await expect(page).toHaveURL(/\/app\/workspace\/overview$/)
    await expect(page.locator('.app-shell')).toHaveAttribute('data-v', 'home')

    await page.goto('/app/auth/initialize')
    await expect(page).toHaveURL(/\/app\/workspace\/overview$/)
    await expect(
      page.getByRole('heading', { name: '运营驾驶舱' }),
    ).toBeVisible()
  })
})
