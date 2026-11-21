import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const bootstrapUrl = '**/api/platform/v1/bootstrap'
const appPath = (path: string) => `/app${path}`

async function stubBootstrap(page: Page, authEnabled: boolean) {
  await page.route(bootstrapUrl, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        auth: { enabled: authEnabled },
        version: 'e2e',
        capabilities: [],
      }),
    })
  })
}

test.describe('Issue #809 identity entry and #752 cutover gate', () => {
  test('auth=false never exposes login or administrator initialization', async ({
    page,
  }) => {
    await stubBootstrap(page, false)
    await page.goto(appPath('/auth/login'))
    await expect(page).toHaveURL(/\/workspace\/overview$/)
    await expect(page.getByText('免登录模式')).toBeVisible()

    await page.goto(appPath('/auth/initialize'))
    await expect(page).toHaveURL(/\/workspace\/overview$/)
    await expect(
      page.getByRole('heading', { name: '运营驾驶舱' }),
    ).toBeVisible()
  })

  test('auth=true initializes the first administrator, then establishes a session', async ({
    page,
  }) => {
    await stubBootstrap(page, true)
    let authenticated = false
    let initializePayload: Record<string, unknown> | undefined

    await page.route('**/auth/initialize', async (route) => {
      initializePayload = route.request().postDataJSON() as Record<
        string,
        unknown
      >
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          initialized: true,
          tenant_id: 'acme',
          user_id: 'owner-1',
        }),
      })
    })
    await page.route('**/auth/login', async (route) => {
      authenticated = true
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          authenticated: true,
          tenant_id: 'acme',
          expires_at: '2030-01-01T00:00:00Z',
          csrf_header: 'X-CSRF-Token',
        }),
      })
    })
    await page.route('**/auth/me', async (route) => {
      if (!authenticated) {
        await route.fulfill({
          status: 401,
          contentType: 'application/json',
          body: '{}',
        })
        return
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          tenant_id: 'acme',
          principal_type: 'user',
          user: {
            user_id: 'owner-1',
            email: 'owner@acme.test',
            display_name: 'Owner',
          },
        }),
      })
    })

    await page.goto(appPath('/auth/initialize'))
    await page.getByLabel('租户 ID').fill('acme')
    await page.getByLabel('租户名称').fill('Acme')
    await page.getByLabel('管理员邮箱').fill('owner@acme.test')
    await page.getByLabel('管理员名称').fill('Owner')
    await page.getByLabel('密码（至少 12 位）').fill('correct-horse-battery')
    await page.getByRole('button', { name: '创建管理员并登录' }).click()

    await expect(page).toHaveURL(/\/workspace\/overview$/)
    await expect(page.getByText('Owner')).toBeVisible()
    expect(initializePayload).toMatchObject({
      tenant_id: 'acme',
      tenant_name: 'Acme',
      email: 'owner@acme.test',
      display_name: 'Owner',
    })
    expect(String(initializePayload?.password)).toBe('correct-horse-battery')
  })

  test('auth=true rejects a repeated first-admin initialization with 409', async ({
    page,
  }) => {
    await stubBootstrap(page, true)
    await page.route('**/auth/me', async (route) => {
      await route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: '{}',
      })
    })
    await page.route('**/auth/initialize', async (route) => {
      await route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({
          message: 'platform is already initialized',
        }),
      })
    })

    await page.goto(appPath('/auth/initialize'))
    await page.getByLabel('租户 ID').fill('acme')
    await page.getByLabel('租户名称').fill('Acme')
    await page.getByLabel('管理员邮箱').fill('owner@acme.test')
    await page.getByLabel('管理员名称').fill('Owner')
    await page.getByLabel('密码（至少 12 位）').fill('correct-horse-battery')
    await page.getByRole('button', { name: '创建管理员并登录' }).click()

    await expect(page.getByRole('alert')).toContainText(
      'platform is already initialized',
    )
    await expect(page).toHaveURL(/\/auth\/initialize$/)
  })

  test('login rejects invalid credentials and returns to the requested deep link on success', async ({
    page,
  }) => {
    await stubBootstrap(page, true)
    let authenticated = false
    await page.route('**/auth/me', async (route) => {
      if (!authenticated) {
        await route.fulfill({
          status: 401,
          contentType: 'application/json',
          body: '{}',
        })
        return
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          tenant_id: 'acme',
          principal_type: 'user',
          user: {
            user_id: 'u-1',
            email: 'user@acme.test',
            display_name: 'User',
          },
        }),
      })
    })
    await page.route('**/auth/login', async (route) => {
      const body = route.request().postDataJSON() as { password?: string }
      if (body.password !== 'valid-password') {
        await route.fulfill({
          status: 401,
          contentType: 'application/json',
          body: JSON.stringify({ message: 'invalid credentials' }),
        })
        return
      }
      authenticated = true
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ authenticated: true }),
      })
    })

    await page.goto(appPath('/operations/runs/run-cutover'))
    await expect(page).toHaveURL(/\/auth\/login$/)
    await page.getByLabel('租户 ID').fill('acme')
    await page.getByLabel('邮箱').fill('user@acme.test')
    await page.getByLabel('密码').fill('wrong-password')
    await page.getByRole('button', { name: '登录' }).click()
    await expect(page.getByRole('alert')).toContainText('invalid credentials')

    await page.getByLabel('密码').fill('valid-password')
    await page.getByRole('button', { name: '登录' }).click()
    await expect(page).toHaveURL(/\/operations\/runs\/run-cutover$/)
  })
})
