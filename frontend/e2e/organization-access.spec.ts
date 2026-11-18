import { expect, test } from '@playwright/test'

test('organization route shows an explicit backend contract gap', async ({
  page,
}) => {
  await page.goto('/admin/organization')
  await expect(page.getByRole('heading', { name: '组织与成员' })).toBeVisible()
  await expect(page.getByText('此页面不会展示模拟组织、成员或权限数据')).toBeVisible()
  await expect(page.locator('.access-admin-card')).toHaveCount(3)
  await expect(page.getByText('组织 / 部门 schema 与树查询 API 未核实')).toBeVisible()
  await expect(page.getByRole('button', { name: /邀请成员|创建组织/ })).toHaveCount(0)
})

test('RBAC route documents missing permission and data-scope contracts', async ({
  page,
}) => {
  await page.goto('/admin/access-control')
  await expect(page.getByRole('heading', { name: '角色与权限' })).toBeVisible()
  await expect(page.locator('.access-admin-card')).toHaveCount(3)
  await expect(page.getByText('权限目录 schema、权限键与服务端 authorizer 未核实')).toHaveCount(0)
  await expect(page.getByText('data_scope 枚举、策略继承和过滤语义未定义')).toHaveCount(0)
  await expect(page.getByText('数据范围枚举、策略继承和过滤语义未定义')).toHaveCount(0)
  await expect(page.getByText('未找到')).toHaveCount(0)
})
