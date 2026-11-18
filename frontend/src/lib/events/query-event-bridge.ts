import type { QueryClient, QueryKey } from '@tanstack/react-query'

import type { SseEventBus } from './event-bus'
import type { PlatformEvent } from './types'

export type EventQueryRoute =
  | QueryKey
  | ((event: PlatformEvent) => QueryKey | QueryKey[] | undefined)

export function connectQueryEvents(
  bus: SseEventBus,
  queryClient: QueryClient,
  routes: Record<string, EventQueryRoute>,
): () => void {
  return bus.subscribe((event) => {
    const route = routes[event.type]
    if (!route) return
    const targets = typeof route === 'function' ? route(event) : route
    if (!targets) return
    const queryKeys = Array.isArray(targets[0]) ? (targets as QueryKey[]) : [targets as QueryKey]
    for (const queryKey of queryKeys) {
      void queryClient.invalidateQueries({ queryKey })
    }
  })
}
