import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'

import { connectQueryEvents } from './query-event-bridge'
import type { SseEventBus } from './event-bus'
import type { EventListener, PlatformEvent } from './types'

const event: PlatformEvent<{ status: string }> = {
  event_id: 'evt-1',
  type: 'task.updated',
  entity_id: 'task-1',
  version: 2,
  occurred_at: '2026-11-19T00:00:00Z',
  sequence: 2,
  payload: { status: 'running' },
}

function createTestBus() {
  const listeners = new Set<EventListener>()
  const bus = {
    subscribe(listener: EventListener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
  } as unknown as SseEventBus
  return {
    bus,
    emit: (item: PlatformEvent) => {
      for (const listener of listeners) listener(item)
    },
  }
}

describe('connectQueryEvents', () => {
  it('applies a safe event update to the matching Query Cache entry', () => {
    const client = new QueryClient()
    client.setQueryData(['tasks', 'task-1'], {
      id: 'task-1',
      status: 'queued',
    })
    const testBus = createTestBus()
    const disconnect = connectQueryEvents(testBus.bus, client, {
      'task.updated': {
        queryKey: (item) => ['tasks', item.entity_id],
        update: (current, item) => ({
          ...(current as object),
          ...(item.payload as object),
          version: item.version,
        }),
      },
    })

    testBus.emit(event)

    expect(client.getQueryData(['tasks', 'task-1'])).toEqual({
      id: 'task-1',
      status: 'running',
      version: 2,
    })
    disconnect()
    client.clear()
  })

  it('invalidates the mapped query when no updater is declared', () => {
    const client = new QueryClient()
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    const testBus = createTestBus()
    const disconnect = connectQueryEvents(testBus.bus, client, {
      'task.updated': ['tasks', 'task-1'],
    })

    testBus.emit(event)

    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ['tasks', 'task-1'],
    })
    disconnect()
    client.clear()
  })

  it('unsubscribes cleanly and ignores unknown event types', () => {
    const client = new QueryClient()
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    const testBus = createTestBus()
    const disconnect = connectQueryEvents(testBus.bus, client, {})

    testBus.emit(event)
    disconnect()
    testBus.emit(event)

    expect(invalidate).not.toHaveBeenCalled()
    client.clear()
  })

  it('does not let an older entity version overwrite newer query data', () => {
    const client = new QueryClient()
    client.setQueryData(['tasks', 'task-1'], {
      id: 'task-1',
      status: 'completed',
      version: 3,
    })
    const testBus = createTestBus()
    const disconnect = connectQueryEvents(testBus.bus, client, {
      'task.updated': {
        queryKey: (item) => ['tasks', item.entity_id],
        update: (current, item) => ({
          ...(current as object),
          ...(item.payload as object),
          version: item.version,
        }),
      },
    })

    testBus.emit(event)

    expect(client.getQueryData(['tasks', 'task-1'])).toEqual({
      id: 'task-1',
      status: 'completed',
      version: 3,
    })
    disconnect()
    client.clear()
  })
})
