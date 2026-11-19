import { expect, test } from '@playwright/test'

const officeState = {
  ok: true,
  data: {
    generated_at: '2026-11-19T10:00:00+08:00',
    metrics: { total: 125, open: 3, closed: 122, done: 119, agents: 4 },
    agents: [
      { name: 'agent-research', title: '市场分析助手', desc: '市场研究与竞品分析', command: 'research' },
      { name: 'agent-code', title: '代码助手', desc: '代码审查与实现', command: 'code' },
      { name: 'agent-docs', title: '文档总结专家', desc: '知识处理与摘要', command: 'docs' },
    ],
    llm: { running: ['agent-research'], model: 'test-model', tokens_today: 18200 },
    tasks: [
      { id: 'task-01', title: '市场分析报告生成', state: '执行中', owner: 'agent-research', db_status: 'doing' },
      { id: 'task-02', title: '产品方案评审', state: '等待审批', owner: 'agent-code', db_status: 'blocked' },
      { id: 'task-03', title: '客户需求分析', state: '已完成', owner: 'agent-docs', db_status: 'done' },
    ],
    runs: [],
    events: {
      pending: 1,
      list: [
        { at: '2026-11-19T09:45:00+08:00', type: 'run.succeeded', text: '竞品分析报告运行完成', task: 'task-01' },
        { at: '2026-11-19T09:32:00+08:00', type: 'agent.updated', text: '数据分析专家配置更新', task: 'agent-data' },
        { at: '2026-11-19T09:10:00+08:00', type: 'workflow.created', text: '新增市场调研 Workflow', task: 'workflow-01' },
      ],
    },
  },
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/platform/v1/bootstrap', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ auth: { enabled: false }, version: 'round-3', capabilities: [] }),
  }))
  await page.route('**/api/platform/v1/office/state', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(officeState),
  }))
})

test('Round 3 home visual snapshot · 1536×1024', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1536, height: 1024 })
  await page.goto('/workspace/overview')
  await expect(page.locator('.r3-home')).toBeVisible()
  await expect(page.getByRole('heading', { name: '欢迎回来！' })).toBeVisible()
  await expect(page.getByText('市场分析报告生成')).toBeVisible()
  await expect(page.getByText('市场分析助手')).toBeVisible()
  const screenshot = await page.screenshot({ fullPage: true })
  await testInfo.attach('r3-home-1536x1024', { body: screenshot, contentType: 'image/png' })
})

test('Round 3 3D office visual snapshot · 1536×1024', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1536, height: 1024 })
  await page.goto('/workspace/office')
  await expect(page.locator('.r3-office')).toBeVisible()
  await expect(page.locator('.r3-office-canvas canvas')).toBeVisible()
  await expect(page.getByText('市场分析助手')).toBeVisible()
  const screenshot = await page.screenshot({ fullPage: true })
  await testInfo.attach('r3-office-1536x1024', { body: screenshot, contentType: 'image/png' })
})
