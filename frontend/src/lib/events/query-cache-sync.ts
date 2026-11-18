import type { QueryClient, QueryKey } from '@tanstack/react-query'

import type { EventListener, PlatformEvent } from './types'
import type { SseEventBus } from './event-bus'

export interface EventCacheBinding {
  queryKey: (event: PlatformEvent) => QueryKey
  update?: (
    current: unknown,
    event: PlatformEvent,
  ) => unknown
}

/**
 * Bridges transport events into TanStack Query without introducing a second
 * entity store. Bindings either update an existing query representation or
 * invalidate the affected query family so it can be refetched through ApiClient.
 */
export function createQueryCacheEventHandler(
  client: QueryClient,
  bindings: Record<string, EventCacheBinding>,
): EventListener {
  return (event) => {
    const binding = bindings[event.type]
    if (!binding) return

    const queryKey = binding.queryKey(event)
    if (binding.update) {
      client.setQueryData(queryKey, (current) =>
        binding.update?.(current, event),
      )
      return
    }

    void client.invalidateQueries({ queryKey })
  }
}

/** Subscribe a bus to the query cache and return the matching unsubscribe. */
export function connectQueryEvents(
  bus: Pick<SseEventBus, 'subscribe'>,
  client: QueryClient,
  bindings: Record<string, EventCacheBinding>,
): () => void {
  return bus.subscribe(createQueryCacheEventHandler(client, bindings))
}
