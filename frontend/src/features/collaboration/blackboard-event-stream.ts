export type BlackboardStreamStatus = 'connecting' | 'connected' | 'reconnecting'

type StreamMessage = MessageEvent<string>
type StreamSource = {
  onopen: ((event: Event) => void) | null
  onerror: ((event: Event) => void) | null
  addEventListener(type: string, listener: EventListener): void
  close(): void
}
type SubscribeOptions = {
  url: string
  onStatus: (status: BlackboardStreamStatus) => void
  onUpdate: (event: StreamMessage) => void
  createSource?: (url: string) => StreamSource
  schedule?: (callback: () => void, delay: number) => ReturnType<typeof setTimeout>
  cancel?: (timer: ReturnType<typeof setTimeout>) => void
}

const RETRY_DELAYS_MS = [1_000, 2_000, 4_000, 8_000, 15_000]

/**
 * Owns Blackboard SSE reconnects. Failed EventSources are closed before a fresh
 * connection is created, avoiding browser-native retries with a stale event ID.
 */
export function subscribeBlackboardEvents({
  url,
  onStatus,
  onUpdate,
  createSource = target => new EventSource(target),
  schedule = (callback, delay) => setTimeout(callback, delay),
  cancel = timer => clearTimeout(timer),
}: SubscribeOptions): () => void {
  let disposed = false
  let retryTimer: ReturnType<typeof setTimeout> | undefined
  let source: StreamSource | undefined
  let attempts = 0

  const connect = () => {
    if (disposed) return
    onStatus('connecting')
    try {
      source = createSource(url)
    } catch {
      retry()
      return
    }
    const current = source
    current.onopen = () => {
      if (disposed || source !== current) return
      attempts = 0
      onStatus('connected')
    }
    current.onerror = () => {
      if (disposed || source !== current) return
      current.close()
      source = undefined
      retry()
    }
    current.addEventListener('blackboard_update', event => {
      if (!disposed && source === current) onUpdate(event as StreamMessage)
    })
  }

  const retry = () => {
    if (disposed || retryTimer !== undefined) return
    onStatus('reconnecting')
    const delay = RETRY_DELAYS_MS[Math.min(attempts, RETRY_DELAYS_MS.length - 1)]
    attempts += 1
    retryTimer = schedule(() => {
      retryTimer = undefined
      connect()
    }, delay)
  }

  connect()
  return () => {
    disposed = true
    if (retryTimer !== undefined) cancel(retryTimer)
    retryTimer = undefined
    source?.close()
    source = undefined
  }
}
