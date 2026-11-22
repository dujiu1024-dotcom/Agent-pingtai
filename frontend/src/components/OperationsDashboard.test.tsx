import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import { OperationsDashboard } from '.'

describe('OperationsDashboard barrel export', () => {
  it('renders the dashboard shell from the components entry point', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const markup = renderToStaticMarkup(
      createElement(
        MemoryRouter,
        null,
        createElement(
          QueryClientProvider,
          { client: queryClient },
          createElement(OperationsDashboard),
        ),
      ),
    )

    expect(markup).toContain('欢迎回来！')
    expect(markup).toContain('我的工作台')
  })
})
