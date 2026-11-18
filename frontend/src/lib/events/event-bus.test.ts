import { describe, expect, it, vi } from 'vitest'

import { SseEventBus } from './event-bus'

describe('SseEventBus', () => {
  it('decodes events and drops duplicate event ids and stale entity sequences', async () => {
    const payload = {
      event_id: 'evt-2',
      type: 'task.updated',
      entity_id: 'task-1',
      version: 2,
      occurred_at: '2026-11-19T00:00:00Z',
      sequence: 2,
      payload: { status: 'running' },
    }
    const frame = [
      `id: evt-2\ndata: ${JSON.stringify(payload)}\n\n`,
      `id: evt-2\ndata: ${JSON.stringify(payload)}\n\n`,
      `id: evt-1\ndata: ${JSON.stringify({
        ...payload,
        event_id: 'evt-1',
        sequence: 1,
      })}\n\n`,
    ].join('')
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode(frame))
            controller.close()
          },
        }),
        { headers: { 'Content-Type': 'text/event-stream' } },
      ),
    )
    const bus = new SseEventBus({
      url: 'https://example.test/events',
      fetcher,
      reconnectBaseMs: 10,
    })
    const received: string[] = []
    const unsubscribe = bus.subscribe((event) => received.push(event.event_id))

    bus.start()
    await vi.waitFor(() => expect(received).toEqual(['evt-2']))
    expect(bus.getLastEventId()).toBe('evt-2')
    unsubscribe()
    bus.stop()
  })

  it('sends the last event cursor on reconnect', async () => {
    const first = {
      event_id: 'evt-10',
      type: 'run.updated',
      entity_id: 'run-1',
      version: 1,
      occurred_at: '2026-11-19T00:00:00Z',
      sequence: 10,
      payload: {},
    }
    let callCount = 0
    const busRef: { current?: SseEventBus } = {}
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async (_input, init) => {
        callCount += 1
        if (callCount === 1) {
          return new Response(
            new ReadableStream({
              start(controller) {
                controller.enqueue(
                  new TextEncoder().encode(
                    `id: evt-10\ndata: ${JSON.stringify(first)}\n\n`,
                  ),
                )
                controller.close()
              },
            }),
            { headers: { 'Content-Type': 'text/event-stream' } },
          )
        }
        expect(new Headers(init?.headers).get('Last-Event-ID')).toBe('evt-10')
        busRef.current?.stop()
        return new Response(null, { status: 204 })
      })
    const bus = new SseEventBus({
      url: 'https://example.test/events',
      fetcher,
      reconnectBaseMs: 1,
      reconnectMaxMs: 1,
    })
    busRef.current = bus
    bus.start()
    await vi.waitFor(() => expect(callCount).toBeGreaterThan(1))
    expect(bus.getStatus()).toBe('closed')
  })

  it('dispatches the final frame when the stream ends without a blank line', async () => {
    const payload = {
      event_id: 'evt-final',
      type: 'run.completed',
      entity_id: 'run-9',
      version: 4,
      occurred_at: '2026-11-19T00:00:00Z',
      sequence: 4,
      payload: { status: 'completed' },
    }
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(
              new TextEncoder().encode(
                `id: evt-final\ndata: ${JSON.stringify(payload)}`,
              ),
            )
            controller.close()
          },
        }),
      ),
    )
    const bus = new SseEventBus({
      url: 'https://example.test/events',
      fetcher,
    })
    const received: string[] = []
    bus.subscribe((item) => received.push(item.event_id))

    bus.start()
    await vi.waitFor(() => expect(received).toEqual(['evt-final']))
    bus.stop()
  })
})
