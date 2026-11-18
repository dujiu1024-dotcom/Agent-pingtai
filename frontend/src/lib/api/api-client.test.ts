import { describe, expect, it, vi } from 'vitest'

import { ApiClient, ApiConflictError } from './api-client'

describe('ApiClient', () => {
  it('serializes the shared pagination contract', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          data: [{ id: 'task-1' }],
          pagination: {
            page: 2,
            page_size: 25,
            total: 26,
            total_pages: 2,
            has_more: false,
          },
        }),
        { headers: { 'Content-Type': 'application/json' } },
      ),
    )
    const client = new ApiClient({ baseUrl: 'https://example.test/api', fetcher })

    await client.list('/tasks', {
      page: 2,
      page_size: 25,
      sort: '-created_at',
      filters: { status: 'running', archived: false },
    })

    const requestUrl = new URL(fetcher.mock.calls[0][0] as string)
    expect(requestUrl.pathname).toBe('/api/tasks')
    expect(requestUrl.searchParams.get('page')).toBe('2')
    expect(requestUrl.searchParams.get('page_size')).toBe('25')
    expect(requestUrl.searchParams.get('sort')).toBe('-created_at')
    expect(requestUrl.searchParams.get('filters')).toBe(
      JSON.stringify({ status: 'running', archived: false }),
    )
  })

  it('reuses an ETag representation after a 304 response', async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ data: 'fresh' }), {
          headers: { ETag: '"rev-1"', 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 304 }))
    const client = new ApiClient({ baseUrl: 'https://example.test/api', fetcher })

    await expect(client.get('/settings')).resolves.toEqual({ data: 'fresh' })
    await expect(client.get('/settings')).resolves.toEqual({ data: 'fresh' })
    expect(new Headers(fetcher.mock.calls[1][1]?.headers).get('If-None-Match')).toBe(
      '"rev-1"',
    )
  })

  it('adds an idempotency key to mutations and exposes revision conflicts', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({ code: 'REVISION_CONFLICT', message: 'Revision changed' }),
        { status: 412, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    const client = new ApiClient({ baseUrl: 'https://example.test/api', fetcher })

    await expect(client.post('/tasks', { name: 'new' })).rejects.toBeInstanceOf(
      ApiConflictError,
    )
    expect(new Headers(fetcher.mock.calls[0][1]?.headers).get('Idempotency-Key')).toBeTruthy()
  })
})
