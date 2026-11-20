import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/lib/api', () => ({ apiClient: { get: mocks.get } }))

import { ApprovalCenterPage } from './ApprovalCenterPage'

describe('ApprovalCenterPage', () => {
  beforeEach(() => {
    mocks.get.mockReset()
  })

  it('renders an auth-disabled empty state without calling approval APIs', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const html = renderToStaticMarkup(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/admin/approvals']}>
          <ApprovalCenterPage authEnabled={false} />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    expect(html).toContain('当前模式不提供审批数据')
    expect(html).toContain('auth.enabled=false')
    expect(mocks.get).not.toHaveBeenCalled()
    queryClient.clear()
  })
})
