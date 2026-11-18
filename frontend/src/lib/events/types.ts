export interface PlatformEvent<TPayload = unknown> {
  event_id: string
  type: string
  entity_id: string
  version: number
  occurred_at: string
  sequence: number
  payload: TPayload
}

export type EventBusStatus = 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'closed'

export interface EventBusOptions {
  url: string
  headers?: HeadersInit
  credentials?: RequestCredentials
  heartbeatTimeoutMs?: number
  reconnectBaseMs?: number
  reconnectMaxMs?: number
  maxRememberedEvents?: number
  fetcher?: typeof fetch
}

export type EventListener = (event: PlatformEvent) => void
