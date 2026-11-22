import { expect, test } from '@playwright/test'

test('Office 3D scene maps live Agent state and floor metadata', async ({ page }) => {
  await page.route('**/api/platform/v1/bootstrap', (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ auth: { enabled: false }, version: 'e2e', capabilities: [] }),
  }))
  await page.route('**/api/platform/v1/office/state', (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ ok: true, data: {
      floors: [
        { id: 'floor-03', label: '03', name: '创新与协作层', description: '会议与协作' },
        { id: 'floor-02', label: '02', name: '智能体工作层', description: 'Agent 工位' },
        { id: 'floor-01', label: '01', name: '运营与知识层', description: '运营空间' },
      ],
      agents: [{
        agent_id: 'agent-live-01', name: '实时代码助手', status: 'running',
        floor_id: 'floor-02', desk_id: 'desk-03',
        current_task: { task_id: 'task-01', title: '代码审查' },
      }],
      runs: [],
      events: { pending: 2 },
    } }),
  }))

  await page.goto('/workspace/office')
  await expect(page.getByRole('heading', { name: '智能体工作层' })).toBeVisible()
  await expect(page.getByText('实时代码助手')).toBeVisible()
  await expect(page.getByText(/运行中/).first()).toBeVisible()
  await expect(page.locator('.office-canvas canvas')).toBeVisible()
  await expect(page.getByText('实时状态已同步')).toBeVisible()

  await page.getByRole('button', { name: /03 创新与协作层/ }).click()
  await expect(page.getByRole('heading', { name: '创新与协作层' })).toBeVisible()
  await expect(page.getByText('实时代码助手')).toHaveCount(0)
})
