import { afterEach, describe, expect, it, vi } from 'vitest'

import { WebSocketEventBus } from './websocket-event-bus'

class FakeSocket {
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  sent: string[] = []
  close = vi.fn()
  send = (value: string) => this.sent.push(value)
}

const event = (eventId: string, sequence: number) =>
  JSON.stringify({
    event_id: eventId,
    type: 'task.updated',
    entity_id: 'task-1',
    version: sequence,
    occurred_at: '2026-11-22T00:00:00Z',
    sequence,
    payload: { status: 'running' },
  })

describe('WebSocketEventBus', () => {
  afterEach(() => vi.useRealTimers())

  it('validates, deduplicates and sequence-checks events', () => {
    const socket = new FakeSocket()
    const bus = new WebSocketEventBus({
      url: 'wss://example.test/events',
      socketFactory: () => socket as unknown as WebSocket,
    })
    const received: string[] = []
    bus.subscribe((value) => received.push(value.event_id))
    bus.start()
    socket.onopen?.()
    socket.onmessage?.({ data: JSON.stringify({ ...JSON.parse(event('evt-2', 2)), event_id: '' }) })
    socket.onmessage?.({ data: event('evt-2', 2) })
    socket.onmessage?.({ data: event('evt-2', 2) })
    socket.onmessage?.({ data: event('evt-1', 1) })
    socket.onmessage?.({ data: '{invalid' })
    expect(received).toEqual(['evt-2'])
    expect(bus.getLastEventId()).toBe('evt-2')
    bus.stop()
    expect(bus.getStatus()).toBe('closed')
  })

  it('sends a resume cursor when the socket reconnects', () => {
    vi.useFakeTimers()
    const sockets: FakeSocket[] = []
    const bus = new WebSocketEventBus({
      url: 'wss://example.test/events',
      reconnectBaseMs: 5,
      socketFactory: () => {
        const socket = new FakeSocket()
        sockets.push(socket)
        return socket as unknown as WebSocket
      },
    })
    bus.start()
    sockets[0].onopen?.()
    sockets[0].onmessage?.({ data: event('evt-1', 1) })
    sockets[0].onclose?.()
    vi.advanceTimersByTime(5)
    sockets[1].onopen?.()
    expect(sockets[1].sent).toEqual([
      JSON.stringify({ type: 'resume', last_event_id: 'evt-1' }),
    ])
    bus.stop()
  })
})
