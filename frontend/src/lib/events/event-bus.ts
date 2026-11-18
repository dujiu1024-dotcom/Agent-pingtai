import type {
  EventBusOptions,
  EventBusStatus,
  EventListener,
  PlatformEvent,
} from './types'

interface SseFrame {
  id?: string
  event?: string
  data: string[]
}

export class SseEventBus {
  private readonly options: Required<
    Pick<
      EventBusOptions,
      | 'heartbeatTimeoutMs'
      | 'reconnectBaseMs'
      | 'reconnectMaxMs'
      | 'maxRememberedEvents'
    >
  > &
    EventBusOptions
  private readonly fetcher: typeof fetch
  private readonly listeners = new Set<EventListener>()
  private readonly seenEventIds = new Set<string>()
  private readonly latestSequence = new Map<string, number>()
  private controller?: AbortController
  private running = false
  private status: EventBusStatus = 'idle'
  private lastEventId?: string
  private reconnectAttempt = 0
  private retryOverride?: number
  private heartbeatTimer?: ReturnType<typeof setTimeout>
  private delayTimer?: ReturnType<typeof setTimeout>
  private finishDelay?: () => void
  private frame: SseFrame = { data: [] }
  private buffer = ''

  constructor(options: EventBusOptions) {
    this.options = {
      ...options,
      heartbeatTimeoutMs: options.heartbeatTimeoutMs ?? 45_000,
      reconnectBaseMs: options.reconnectBaseMs ?? 1_000,
      reconnectMaxMs: options.reconnectMaxMs ?? 30_000,
      maxRememberedEvents: options.maxRememberedEvents ?? 2_000,
    }
    this.fetcher = options.fetcher ?? fetch
  }

  subscribe(listener: EventListener): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  getStatus(): EventBusStatus {
    return this.status
  }

  getLastEventId(): string | undefined {
    return this.lastEventId
  }

  start(): void {
    if (this.running) return
    this.running = true
    void this.run()
  }

  stop(): void {
    this.running = false
    this.controller?.abort()
    if (this.delayTimer) clearTimeout(this.delayTimer)
    this.finishDelay?.()
    this.clearHeartbeat()
    this.setStatus('closed')
  }

  private async run(): Promise<void> {
    while (this.running) {
      this.controller = new AbortController()
      this.setStatus(
        this.reconnectAttempt === 0 ? 'connecting' : 'reconnecting',
      )
      try {
        await this.connect(this.controller.signal)
        if (this.running) throw new Error('SSE stream ended')
      } catch {
        if (!this.running) break
      } finally {
        this.clearHeartbeat()
      }
      if (!this.running) break
      this.reconnectAttempt += 1
      const exponential = Math.min(
        this.options.reconnectBaseMs * 2 ** (this.reconnectAttempt - 1),
        this.options.reconnectMaxMs,
      )
      await this.delay(this.retryOverride ?? exponential)
    }
    if (!this.running) this.setStatus('closed')
  }

  private async connect(signal: AbortSignal): Promise<void> {
    const headers = new Headers(this.options.headers)
    headers.set('Accept', 'text/event-stream')
    headers.set('Cache-Control', 'no-cache')
    if (this.lastEventId) headers.set('Last-Event-ID', this.lastEventId)
    const response = await this.fetcher(this.options.url, {
      method: 'GET',
      headers,
      credentials: this.options.credentials ?? 'same-origin',
      cache: 'no-store',
      signal,
    })
    if (!response.ok || !response.body) {
      throw new Error(`SSE connection failed: HTTP ${response.status}`)
    }
    this.reconnectAttempt = 0
    this.setStatus('connected')
    this.armHeartbeat(signal)
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    try {
      while (this.running) {
        const { done, value } = await reader.read()
        if (done) break
        this.buffer += decoder.decode(value, { stream: true })
        let newline = this.buffer.indexOf('\n')
        while (newline >= 0) {
          let line = this.buffer.slice(0, newline)
          this.buffer = this.buffer.slice(newline + 1)
          if (line.endsWith('\r')) line = line.slice(0, -1)
          this.consumeLine(line, signal)
          newline = this.buffer.indexOf('\n')
        }
      }
    } finally {
      await reader.cancel().catch(() => undefined)
      this.buffer = ''
      this.frame = { data: [] }
    }
  }

  private consumeLine(line: string, signal: AbortSignal): void {
    if (line === '') {
      this.dispatchFrame()
      return
    }
    if (line.startsWith(':')) {
      this.armHeartbeat(signal)
      return
    }
    const separator = line.indexOf(':')
    const field = separator < 0 ? line : line.slice(0, separator)
    const value =
      separator < 0 ? '' : line.slice(separator + 1).replace(/^ /, '')
    if (field === 'data') this.frame.data.push(value)
    else if (field === 'id' && !value.includes('\0')) this.frame.id = value
    else if (field === 'event') this.frame.event = value
    else if (field === 'retry' && /^\d+$/.test(value)) {
      this.retryOverride = Math.min(Number(value), this.options.reconnectMaxMs)
    }
  }

  private dispatchFrame(): void {
    const frame = this.frame
    this.frame = { data: [] }
    if (frame.event === 'heartbeat') {
      this.armHeartbeat(this.controller?.signal)
      return
    }
    if (frame.data.length === 0) return
    let event: PlatformEvent
    try {
      event = JSON.parse(frame.data.join('\n')) as PlatformEvent
    } catch {
      return
    }
    const eventId = frame.id ?? event.event_id
    if (
      !eventId ||
      typeof event.type !== 'string' ||
      typeof event.entity_id !== 'string' ||
      !Number.isFinite(event.version) ||
      !Number.isFinite(event.sequence) ||
      typeof event.occurred_at !== 'string'
    ) return
    if (this.seenEventIds.has(eventId)) return
    const previousSequence = this.latestSequence.get(event.entity_id)
    if (
      previousSequence !== undefined &&
      event.sequence <= previousSequence
    ) {
      return
    }

    this.rememberEvent(eventId)
    this.lastEventId = eventId
    this.latestSequence.set(event.entity_id, event.sequence)
    for (const listener of this.listeners) {
      listener({ ...event, event_id: eventId })
    }
  }

  private rememberEvent(eventId: string): void {
    this.seenEventIds.add(eventId)
    if (this.seenEventIds.size > this.options.maxRememberedEvents) {
      const oldest = this.seenEventIds.values().next()
        .value as string | undefined
      if (oldest) this.seenEventIds.delete(oldest)
    }
  }

  private armHeartbeat(signal?: AbortSignal): void {
    this.clearHeartbeat()
    this.heartbeatTimer = setTimeout(() => {
      if (!signal?.aborted) {
        this.controller?.abort(new Error('SSE heartbeat timeout'))
      }
    }, this.options.heartbeatTimeoutMs)
  }

  private clearHeartbeat(): void {
    if (this.heartbeatTimer) clearTimeout(this.heartbeatTimer)
    this.heartbeatTimer = undefined
  }

  private setStatus(status: EventBusStatus): void {
    this.status = status
  }

  private delay(ms: number): Promise<void> {
    return new Promise((resolve) => {
      this.finishDelay = resolve
      this.delayTimer = setTimeout(resolve, ms)
    }).finally(() => {
      if (this.delayTimer) clearTimeout(this.delayTimer)
      this.delayTimer = undefined
      this.finishDelay = undefined
    })
  }
}

export function createEventBus(options: EventBusOptions): SseEventBus {
  return new SseEventBus(options)
}
