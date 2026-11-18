import type { QueryClient, QueryKey } from '@tanstack/react-query'

import type { SseEventBus } from './event-bus'
import type { PlatformEvent } from './types'

export interface EventQueryBinding {
  queryKey: QueryKey | ((event: PlatformEvent) => QueryKey)
  update?: (current: unknown, event: PlatformEvent) => unknown
  shouldApply?: (current: unknown, event: PlatformEvent) => boolean
}

export type EventQueryRoute =
  | QueryKey
  | ((event: PlatformEvent) => QueryKey | QueryKey[] | undefined)
  | EventQueryBinding

export function connectQueryEvents(
  bus: SseEventBus,
  queryClient: QueryClient,
  routes: Record<string, EventQueryRoute>,
): () => void {
  return bus.subscribe((event) => {
    const route = routes[event.type]
    if (!route) return

    if (!Array.isArray(route) && typeof route === 'object') {
      const binding = route as EventQueryBinding
      const queryKey =
        typeof binding.queryKey === 'function'
          ? binding.queryKey(event)
          : binding.queryKey
      if (binding.update) {
        queryClient.setQueryData(queryKey, (current) => {
          if (binding.shouldApply && !binding.shouldApply(current, event)) {
            return current
          }
          if (
            current &&
            typeof current === 'object' &&
            'version' in current &&
            typeof current.version === 'number' &&
            current.version > event.version
          ) {
            return current
          }
          return binding.update?.(current, event)
        })
      } else {
        void queryClient.invalidateQueries({ queryKey })
      }
      return
    }

    const targets = typeof route === 'function' ? route(event) : route
    if (!targets) return
    const queryKeys = Array.isArray(targets[0])
      ? (targets as QueryKey[])
      : [targets as QueryKey]
    for (const queryKey of queryKeys) {
      void queryClient.invalidateQueries({ queryKey })
    }
  })
}
