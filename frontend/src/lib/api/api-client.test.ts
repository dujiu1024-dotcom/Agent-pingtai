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
    const client = new ApiClient({
      baseUrl: 'https://example.test/api',
      fetcher,
    })

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
    const client = new ApiClient({
      baseUrl: 'https://example.test/api',
      fetcher,
    })

    await expect(client.get('/settings')).resolves.toEqual({ data: 'fresh' })
    await expect(client.get('/settings')).resolves.toEqual({ data: 'fresh' })
    expect(
      new Headers(fetcher.mock.calls[1][1]?.headers).get('If-None-Match'),
    ).toBe('"rev-1"')
  })

  it('adds an idempotency key and exposes revision conflicts', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          code: 'REVISION_CONFLICT',
          message: 'Revision changed',
        }),
        { status: 412, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    const client = new ApiClient({
      baseUrl: 'https://example.test/api',
      fetcher,
    })

    await expect(
      client.post('/tasks', { name: 'new' }, { ifMatch: '"rev-1"' }),
    ).rejects.toBeInstanceOf(ApiConflictError)
    const headers = new Headers(fetcher.mock.calls[0][1]?.headers)
    expect(headers.get('Idempotency-Key')).toBeTruthy()
    expect(headers.get('If-Match')).toBe('"rev-1"')
  })

  it('preserves caller cancellation instead of reporting a timeout', async () => {
    const fetcher = vi.fn<typeof fetch>(
      (_input, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener(
            'abort',
            () => reject(init.signal?.reason),
            { once: true },
          )
        }),
    )
    const client = new ApiClient({
      baseUrl: 'https://example.test/api',
      fetcher,
    })
    const controller = new AbortController()
    const request = client.get('/tasks', { signal: controller.signal })
    controller.abort(new DOMException('Cancelled by caller', 'AbortError'))

    await expect(request).rejects.toMatchObject({ name: 'AbortError' })
  })

  it('uses a caller-provided idempotency key and surfaces request ids', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          code: 'RATE_LIMITED',
          message: 'Try again later',
          request_id: 'req-123',
          retryable: true,
        }),
        { status: 429, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    const client = new ApiClient({
      baseUrl: 'https://example.test/api',
      fetcher,
    })

    await expect(
      client.post('/tasks', {}, { idempotencyKey: 'logical-op-1' }),
    ).rejects.toMatchObject({
      code: 'RATE_LIMITED',
      requestId: 'req-123',
      retryable: true,
    })
    expect(
      new Headers(fetcher.mock.calls[0][1]?.headers).get('Idempotency-Key'),
    ).toBe('logical-op-1')
  })

  it('clears ETag validators after a successful mutation', async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ data: 'v1' }), {
          headers: { ETag: '"v1"', 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ data: 'v2' }), {
          headers: { ETag: '"v2"', 'Content-Type': 'application/json' },
        }),
      )
    const client = new ApiClient({
      baseUrl: 'https://example.test/api',
      fetcher,
    })

    await client.get('/tasks/task-1')
    await client.post('/tasks/task-1/refresh', {})
    await client.get('/tasks/task-1')

    expect(
      new Headers(fetcher.mock.calls[2][1]?.headers).get('If-None-Match'),
    ).toBeNull()
  })

  it('converts a timeout into a retryable API error', async () => {
    vi.useFakeTimers()
    const fetcher = vi.fn<typeof fetch>(
      (_input, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener(
            'abort',
            () => reject(init.signal?.reason),
            { once: true },
          )
        }),
    )
    const client = new ApiClient({
      baseUrl: 'https://example.test/api',
      timeoutMs: 25,
      fetcher,
    })

    const request = client.get('/slow')
    const assertion = expect(request).rejects.toMatchObject({
      code: 'REQUEST_TIMEOUT',
      retryable: true,
    })
    await vi.advanceTimersByTimeAsync(25)
    await assertion
    vi.useRealTimers()
  })

})
