import { expect, test } from '@playwright/test'

const requiredEnvironment = [
  'PLATFORM_E2E_BASE_URL',
  'PLATFORM_E2E_TENANT_ID',
  'PLATFORM_E2E_EMAIL',
  'PLATFORM_E2E_PASSWORD',
  'PLATFORM_E2E_MODEL_TEMPLATE_ID',
  'PLATFORM_E2E_PROJECT_ID',
  'PLATFORM_E2E_SESSION_ID',
] as const

const missing = requiredEnvironment.filter((key) => !process.env[key])

test.describe('Issue #14 live model, Gateway and object-storage journey', () => {
  test.skip(
    missing.length > 0,
    `requires a deployed integration environment and dedicated test credentials; missing: ${missing.join(', ')}`,
  )

  test('login → publish model-backed Agent → execute through Gateway → upload to object storage', async ({
    page,
  }) => {
    const baseUrl = process.env.PLATFORM_E2E_BASE_URL!
    const tenantId = process.env.PLATFORM_E2E_TENANT_ID!
    const email = process.env.PLATFORM_E2E_EMAIL!
    const password = process.env.PLATFORM_E2E_PASSWORD!
    const modelTemplateId = process.env.PLATFORM_E2E_MODEL_TEMPLATE_ID!
    const projectId = process.env.PLATFORM_E2E_PROJECT_ID!
    const sessionId = process.env.PLATFORM_E2E_SESSION_ID!
    const runTag = `issue14-${Date.now()}`
    const agentId = `${runTag}-agent`

    expect(new URL(baseUrl).protocol).toBe('https:')
    expect(new URL(baseUrl).origin).toBe(new URL(page.url() || baseUrl).origin)

    // No route interception or API mocks are installed in this live acceptance.
    const failedApiResponses: string[] = []
    page.on('response', (response) => {
      if (
        response.url().includes('/api/platform/v1/') &&
        response.status() >= 500
      ) {
        failedApiResponses.push(`${response.status()} ${response.url()}`)
      }
    })

    await page.goto('/auth/login')
    await expect(page.getByRole('heading', { name: /登录/ })).toBeVisible()
    await page.getByLabel('租户 ID').fill(tenantId)
    await page.getByLabel('邮箱').fill(email)
    await page.getByLabel('密码').fill(password)
    await page.getByRole('button', { name: '登录' }).click()
    await expect(page).toHaveURL(/workspace\/overview/, { timeout: 30_000 })

    // Create and publish an Agent whose model binding points at the deployed model template.
    await page.goto('/admin/agents')
    await page.getByLabel('新 Agent ID').fill(agentId)
    await page.getByRole('button', { name: '新建 Agent' }).click()
    await expect(page).toHaveURL(new RegExp(`/admin/agents/${agentId}$`))
    await page.getByLabel('名称').fill(`${runTag} model gateway acceptance`)
    await page.getByLabel('默认模型模板引用').fill(modelTemplateId)
    await page.getByLabel('描述').fill('Issue #14 live model/Gateway browser acceptance')
    await page.getByLabel('指令（Instructions）').fill(
      'Answer the user request briefly. Include the marker LIVE_MODEL_GATEWAY_OK in the response.',
    )
    await page.getByRole('button', { name: '保存并发布' }).click()
    await expect(page.getByText(/当前生效版本 v\d+/)).toBeVisible({
      timeout: 30_000,
    })

    // Submit through the authenticated Workbench API in the real browser session;
    // the deployed worker, Gateway and model provider execute the task.
    const submit = await page.request.post('/api/platform/v1/workbench/tasks', {
      data: {
        agent_id: agentId,
        project_id: projectId,
        session_id: sessionId,
        input: {
          prompt: `Issue #14 acceptance ${runTag}: reply with LIVE_MODEL_GATEWAY_OK`,
        },
      },
    })
    expect(submit.ok(), await submit.text()).toBeTruthy()
    const task = (await submit.json()) as {
      task_id: string
      run_id?: string | null
    }
    expect(task.task_id).toBeTruthy()

    await page.goto('/workspace/tasks')
    await expect(page.getByRole('heading', { name: '任务指挥中心' })).toBeVisible()
    await page.getByLabel('搜索任务').fill(task.task_id)
    await expect(page.getByRole('link', { name: new RegExp(task.task_id) })).toBeVisible({
      timeout: 30_000,
    })
    await page.goto(`/operations/tasks/${encodeURIComponent(task.task_id)}`)
    await expect(page.getByRole('heading', { name: '任务详情' })).toBeVisible()

    // Wait for a real worker result. A merely queued task is not a successful model/Gateway run.
    await expect
      .poll(
        async () => {
          const response = await page.request.get(
            `/api/platform/v1/workbench/tasks/${encodeURIComponent(task.task_id)}`,
          )
          if (!response.ok()) return `http-${response.status()}`
          const value = (await response.json()) as { status?: string }
          return value.status ?? 'missing-status'
        },
        { timeout: 180_000, intervals: [2_000, 5_000, 10_000] },
      )
      .toMatch(/^(completed|succeeded|awaiting_approval|approval_required)$/)

    // Exercise the real object-storage upload path via the browser UI.
    await page.goto('/operations/knowledge-bases')
    await expect(page.getByRole('heading', { name: '知识库管理' })).toBeVisible()
    const uploadResponse = page.waitForResponse(
      (response) =>
        response.url().includes('/api/platform/v1/knowledge/documents/upload') &&
        response.request().method() === 'POST',
      { timeout: 120_000 },
    )
    await page.locator('#knowledge-upload-file').setInputFiles({
      name: `${runTag}.md`,
      mimeType: 'text/markdown',
      buffer: Buffer.from(`# ${runTag}\n\nObject storage live upload acceptance.\n`),
    })
    await page.getByRole('button', { name: '上传并建立索引' }).click()
    const uploaded = await uploadResponse
    expect(uploaded.ok(), await uploaded.text()).toBeTruthy()
    await expect(page.getByText('文档解析、切片与索引处理已完成。')).toBeVisible({
      timeout: 120_000,
    })
    await expect(page.getByText(`${runTag}`, { exact: false })).toBeVisible()

    expect(failedApiResponses).toEqual([])
  })
})
