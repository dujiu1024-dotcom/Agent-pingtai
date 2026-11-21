import { expect, test } from '@playwright/test'

test('Home workspace renders API-backed task, popular Agent and activity panels', async ({ page }) => {
  await page.route('**/api/platform/v1/bootstrap', (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ auth: { enabled: false }, version: 'e2e', capabilities: [] }),
  }))
  await page.route('**/api/platform/v1/workbench/tasks', (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ items: [
      { task_id: 'task-running', status: 'running', input: { title: '正在运行的任务' }, agent_id: 'agent-coder', duration_ms: 12000 },
      { task_id: 'task-approval', status: 'waiting_approval', input: { title: '等待审批的任务' }, agent_id: 'agent-analyst' },
      { task_id: 'task-done', status: 'succeeded', input: { title: '已完成的任务' }, duration_ms: 45000 },
    ] }),
  }))
  await page.route('**/api/platform/v1/registry/agent', (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ items: [
      { item_id: 'agent-coder', status: 'active', definition: { name: '代码助手', description: '代码分析与生成' } },
      { item_id: 'agent-analyst', status: 'registered', definition: { name: '市场分析助手', description: '市场研究' } },
    ] }),
  }))
  await page.route('**/api/platform/v1/runs', (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ items: [
      { run_id: 'run-1', task_id: 'task-running', agent_id: 'agent-coder', status: 'running', updated_at: '2026-11-22T10:00:00Z' },
      { run_id: 'run-2', task_id: 'task-done', agent_id: 'agent-coder', status: 'succeeded', finished_at: '2026-11-22T09:00:00Z' },
      { run_id: 'run-3', task_id: 'task-approval', agent_id: 'agent-analyst', status: 'waiting_approval', updated_at: '2026-11-22T08:00:00Z' },
    ] }),
  }))

  await page.goto('/')
  await expect(page.getByRole('heading', { name: '欢迎回来！' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '任务列表' })).toBeVisible()
  await expect(page.getByText('正在运行的任务')).toBeVisible()
  await expect(page.getByRole('heading', { name: '热门 Agent' })).toBeVisible()
  await expect(page.getByText('代码助手')).toBeVisible()
  await expect(page.getByRole('heading', { name: '最近动态' })).toBeVisible()

  await page.getByRole('tab', { name: /运行中/ }).click()
  await expect(page.getByText('正在运行的任务')).toBeVisible()
  await expect(page.getByText('等待审批的任务')).toHaveCount(0)
  await page.getByRole('tab', { name: /等待审批/ }).click()
  await expect(page.getByText('等待审批的任务')).toBeVisible()
  await expect(page.getByText('正在运行的任务')).toHaveCount(0)
  await page.getByRole('link', { name: /Workflow/ }).toBeVisible()
})
