import type {
  EventBusPort,
  EventBusStatus,
  EventListener,
  PlatformEvent,
} from './types'

export interface WebSocketEventBusOptions {
  url: string
  protocols?: string | string[]
  reconnectBaseMs?: number
  reconnectMaxMs?: number
  maxRememberedEvents?: number
  socketFactory?: (
    url: string,
    protocols?: string | string[],
  ) => WebSocket
}

/** WebSocket transport with the same event contract and lifecycle as SSE. */
export class WebSocketEventBus implements EventBusPort {
  private readonly options: Required<
    Pick<
      WebSocketEventBusOptions,
      'reconnectBaseMs' | 'reconnectMaxMs' | 'maxRememberedEvents'
    >
  > &
    WebSocketEventBusOptions
  private readonly listeners = new Set<EventListener>()
  private readonly seenEventIds = new Set<string>()
  private readonly latestSequence = new Map<string, number>()
  private socket?: WebSocket
  private reconnectTimer?: ReturnType<typeof setTimeout>
  private running = false
  private attempt = 0
  private status: EventBusStatus = 'idle'
  private lastEventId?: string

  constructor(options: WebSocketEventBusOptions) {
    this.options = {
      ...options,
      reconnectBaseMs: options.reconnectBaseMs ?? 1_000,
      reconnectMaxMs: options.reconnectMaxMs ?? 30_000,
      maxRememberedEvents: options.maxRememberedEvents ?? 2_000,
    }
  }

  subscribe(listener: EventListener): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  getStatus(): EventBusStatus {
    return this.status
  }

  start(): void {
    if (this.running) return
    this.running = true
    this.connect()
  }

  stop(): void {
    this.running = false
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.reconnectTimer = undefined
    const socket = this.socket
    this.socket = undefined
    socket?.close(1000, 'client stopped')
    this.setStatus('closed')
  }

  private connect(): void {
    if (!this.running) return
    this.setStatus(this.attempt === 0 ? 'connecting' : 'reconnecting')
    const factory =
      this.options.socketFactory ??
      ((url: string, protocols?: string | string[]) =>
        new WebSocket(url, protocols))
    let socket: WebSocket
    try {
      socket = factory(this.options.url, this.options.protocols)
    } catch {
      this.scheduleReconnect()
      return
    }
    this.socket = socket
    socket.onopen = () => {
      if (!this.running || this.socket !== socket) return
      this.attempt = 0
      this.setStatus('connected')
      if (this.lastEventId) {
        socket.send(JSON.stringify({ type: 'resume', last_event_id: this.lastEventId }))
      }
    }
    socket.onmessage = (message) => {
      if (typeof message.data !== 'string') return
      this.dispatch(message.data)
    }
    socket.onerror = () => {
      // onclose owns reconnection to avoid duplicate reconnect timers.
    }
    socket.onclose = () => {
      if (this.socket === socket) this.socket = undefined
      if (this.running) this.scheduleReconnect()
    }
  }

  private dispatch(raw: string): void {
    let value: unknown
    try {
      value = JSON.parse(raw)
    } catch {
      return
    }
    if (!value || typeof value !== 'object') return
    const event = value as Partial<PlatformEvent>
    if (
      typeof event.event_id !== 'string' ||
      typeof event.type !== 'string' ||
      typeof event.entity_id !== 'string' ||
      typeof event.version !== 'number' ||
      typeof event.sequence !== 'number' ||
      typeof event.occurred_at !== 'string' ||
      !('payload' in event)
    ) {
      return
    }
    if (this.seenEventIds.has(event.event_id)) return
    const previous = this.latestSequence.get(event.entity_id)
    if (previous !== undefined && event.sequence <= previous) return

    this.seenEventIds.add(event.event_id)
    this.latestSequence.set(event.entity_id, event.sequence)
    this.lastEventId = event.event_id
    while (this.seenEventIds.size > this.options.maxRememberedEvents) {
      const oldest = this.seenEventIds.values().next().value
      if (oldest === undefined) break
      this.seenEventIds.delete(oldest)
    }
    for (const listener of this.listeners) {
      try {
        listener(event as PlatformEvent)
      } catch {
        // One consumer must not prevent delivery to other subscribers.
      }
    }
  }

  private scheduleReconnect(): void {
    if (!this.running || this.reconnectTimer) return
    this.setStatus('reconnecting')
    const delay = Math.min(
      this.options.reconnectBaseMs * 2 ** this.attempt,
      this.options.reconnectMaxMs,
    )
    this.attempt += 1
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = undefined
      this.connect()
    }, delay)
  }

  private setStatus(status: EventBusStatus): void {
    this.status = status
  }
}

export function createWebSocketEventBus(
  options: WebSocketEventBusOptions,
): WebSocketEventBus {
  return new WebSocketEventBus(options)
}
