import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { StaticRouter } from 'react-router-dom/server'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@/lib/api', () => ({
  ApiError: class ApiError extends Error {
    status: number
    constructor(message: string, status: number) {
      super(message)
      this.status = status
    }
  },
  apiClient: { get: mocks.get, post: mocks.post },
}))

import { parseCanaryPercentage, PromptRegistryPage } from './PromptRegistryPage'

describe('PromptRegistryPage', () => {
  it('accepts only finite canary percentages in the open-closed range (0, 100]', () => {
    expect(parseCanaryPercentage('10')).toBe(10)
    expect(parseCanaryPercentage('0.1')).toBe(0.1)
    expect(parseCanaryPercentage('100')).toBe(100)
    expect(parseCanaryPercentage('0')).toBeNull()
    expect(parseCanaryPercentage('100.1')).toBeNull()
    expect(parseCanaryPercentage('Infinity')).toBeNull()
    expect(parseCanaryPercentage('not-a-number')).toBeNull()
  })

  beforeEach(() => {
    mocks.get.mockReset()
    mocks.post.mockReset()
  })

  it('renders the Prompt Registry workspace without an evaluation gate', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const html = renderToStaticMarkup(
      <QueryClientProvider client={queryClient}>
        <StaticRouter location="/admin/prompts">
          <PromptRegistryPage evaluationGateEnabled={false} />
        </StaticRouter>
      </QueryClientProvider>,
    )

    expect(html).toContain('Prompt 与模板管理')
    expect(html).toContain('新建 Prompt 版本')
    expect(html).toContain('Prompt ID')
    expect(html).toContain('评测 Gate')
    queryClient.clear()
  })
})
