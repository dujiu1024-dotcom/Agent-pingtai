import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'

import { createQueryCacheEventHandler } from './query-cache-sync'
import type { PlatformEvent } from './types'

const event: PlatformEvent<{ status: string }> = {
  event_id: 'evt-1',
  type: 'task.updated',
  entity_id: 'task-1',
  version: 2,
  occurred_at: '2026-11-19T00:00:00Z',
  sequence: 2,
  payload: { status: 'running' },
}

describe('createQueryCacheEventHandler', () => {
  it('updates the matching TanStack Query cache entry in place', () => {
    const client = new QueryClient()
    client.setQueryData(['tasks', 'task-1'], { id: 'task-1', status: 'queued' })
    const handler = createQueryCacheEventHandler(client, {
      'task.updated': {
        queryKey: (item) => ['tasks', item.entity_id],
        update: (current, item) => ({
          ...(current as object),
          ...(item.payload as object),
          version: item.version,
        }),
      },
    })

    handler(event)

    expect(client.getQueryData(['tasks', 'task-1'])).toEqual({
      id: 'task-1',
      status: 'running',
      version: 2,
    })
    client.clear()
  })

  it('invalidates a query family when no safe updater is registered', async () => {
    const client = new QueryClient()
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    const handler = createQueryCacheEventHandler(client, {
      'task.updated': {
        queryKey: (item) => ['tasks', item.entity_id],
      },
    })

    handler(event)
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ['tasks', 'task-1'],
    })
    client.clear()
  })

  it('ignores event types without a registered query binding', () => {
    const client = new QueryClient()
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    const handler = createQueryCacheEventHandler(client, {})

    handler(event)

    expect(invalidate).not.toHaveBeenCalled()
    client.clear()
  })
})
