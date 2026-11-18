import { describe, expect, it } from 'vitest'
import { matchRoutes } from 'react-router-dom'

import { authRoutes, getAllRoutes, getVisibleNavigation, isCanonicalRouteSetValid, pageRoutes } from './route-config'

describe('route configuration', () => {
  it('keeps canonical page ids and paths unique', () => {
    expect(isCanonicalRouteSetValid()).toBe(true)
  })

  it('uses the frozen dashboard route as the default route target', () => {
    expect(getAllRoutes(false).find((route) => route.pageId === 'WS-OVERVIEW')?.path).toBe('/workspace/overview')
  })

  it('does not expose identity and RBAC navigation in no-auth mode', () => {
    const paths = getVisibleNavigation(false).flatMap((group) => group.items.map((item) => item.path))
    expect(paths).not.toContain('/admin/organization')
    expect(paths).not.toContain('/admin/access-control')
    expect(getAllRoutes(false)).not.toEqual(expect.arrayContaining(authRoutes))
  })

  it('exposes identity and RBAC routes only when server mode enables auth', () => {
    expect(getAllRoutes(true)).toEqual(expect.arrayContaining(authRoutes))
    expect(getVisibleNavigation(true).flatMap((group) => group.items.map((item) => item.path))).toContain('/admin/access-control')
  })

  it('matches entity detail deep links with stable id parameters', () => {
    const routeObjects = pageRoutes.map((route) => ({ path: route.path }))
    expect(matchRoutes(routeObjects, '/operations/tasks/task-123')?.[0].params.taskId).toBe('task-123')
    expect(matchRoutes(routeObjects, '/operations/runs/run-456')?.[0].params.runId).toBe('run-456')
  })
})
