import { afterEach, describe, expect, it, vi } from 'vitest'
import { subscribeBlackboardEvents } from './blackboard-event-stream'

class FakeEventSource {
  onopen: ((event: Event) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  closed = false
  listeners = new Map<string, EventListener[]>()

  addEventListener(type: string, listener: EventListener) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener])
  }

  close() {
    this.closed = true
  }

  open() {
    this.onopen?.(new Event('open'))
  }

  fail() {
    this.onerror?.(new Event('error'))
  }

  emit(type: string, data: string) {
    const event = new MessageEvent(type, { data })
    for (const listener of this.listeners.get(type) ?? []) listener(event)
  }
}

afterEach(() => vi.useRealTimers())

describe('Blackboard SSE reconnect', () => {
  it('reconnects with bounded exponential backoff and returns to connected state', () => {
    vi.useFakeTimers()
    const sources: FakeEventSource[] = []
    const statuses: string[] = []
    const cleanup = subscribeBlackboardEvents({
      url: '/blackboard/events/stream?scope=run&scope_id=run-1',
      createSource: () => {
        const source = new FakeEventSource()
        sources.push(source)
        return source
      },
      onStatus: status => statuses.push(status),
      onUpdate: () => undefined,
    })

    sources[0].open()
    sources[0].fail()
    expect(sources[0].closed).toBe(true)
    expect(statuses.at(-1)).toBe('reconnecting')
    vi.advanceTimersByTime(999)
    expect(sources).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(sources).toHaveLength(2)
    sources[1].open()
    expect(statuses.at(-1)).toBe('connected')

    sources[1].fail()
    vi.advanceTimersByTime(1_000)
    expect(sources).toHaveLength(3)
    cleanup()
  })

  it('delivers updates only from the active connection', () => {
    vi.useFakeTimers()
    const sources: FakeEventSource[] = []
    const updates: string[] = []
    const cleanup = subscribeBlackboardEvents({
      url: '/stream',
      createSource: () => {
        const source = new FakeEventSource()
        sources.push(source)
        return source
      },
      onStatus: () => undefined,
      onUpdate: event => updates.push(event.data),
    })

    sources[0].emit('blackboard_update', 'first')
    sources[0].fail()
    sources[0].emit('blackboard_update', 'stale')
    vi.advanceTimersByTime(1_000)
    sources[1].emit('blackboard_update', 'second')
    expect(updates).toEqual(['first', 'second'])
    cleanup()
  })

  it('cancels pending retries and closes the active source on unmount', () => {
    vi.useFakeTimers()
    const sources: FakeEventSource[] = []
    const cleanup = subscribeBlackboardEvents({
      url: '/stream',
      createSource: () => {
        const source = new FakeEventSource()
        sources.push(source)
        return source
      },
      onStatus: () => undefined,
      onUpdate: () => undefined,
    })
    sources[0].fail()
    cleanup()
    vi.advanceTimersByTime(30_000)
    expect(sources).toHaveLength(1)
    expect(sources[0].closed).toBe(true)
  })
})
