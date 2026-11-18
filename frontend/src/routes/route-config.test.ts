import { describe, expect, it } from 'vitest'
import { matchRoutes } from 'react-router-dom'

import { authRoutes, detailRoutes, getAllRoutes, getVisibleNavigation, globalRoutes, isCanonicalRouteSetValid, pageRoutes } from './route-config'

describe('route configuration', () => {
  it('keeps canonical page ids and paths unique', () => {
    expect(isCanonicalRouteSetValid([...pageRoutes, ...globalRoutes, ...authRoutes])).toBe(true)
  })

  it('provides exactly three primary navigation sections', () => {
    expect(getVisibleNavigation(false).map((group) => group.id)).toEqual(['workspace', 'operations', 'administration'])
  })

  it('uses the frozen dashboard route as the default route target', () => {
    expect(getAllRoutes(false).find((route) => route.pageId === 'WS-OVERVIEW')?.path).toBe('/workspace/overview')
  })

  it('does not expose identity and RBAC navigation in no-auth mode', () => {
    const paths = getVisibleNavigation(false).flatMap((group) => group.items.map((item) => item.path))
    expect(paths).not.toContain('/admin/organization')
    expect(paths).not.toContain('/admin/access-control')
    expect(paths.every((path) => !path.includes('/:'))).toBe(true)
    expect(getAllRoutes(false)).not.toEqual(expect.arrayContaining(authRoutes))
  })

  it('shows capability-gated navigation only when bootstrap advertises it', () => {
    const withoutCapability = getVisibleNavigation(true).flatMap((group) => group.items.map((item) => item.path))
    const withCapability = getVisibleNavigation(true, ['prompt_management']).flatMap((group) => group.items.map((item) => item.path))
    expect(withoutCapability).not.toContain('/admin/prompts')
    expect(withCapability).toContain('/admin/prompts')
  })

  it('exposes identity and RBAC routes only when server mode enables auth', () => {
    expect(getAllRoutes(true)).toEqual(expect.arrayContaining(authRoutes))
    expect(getVisibleNavigation(true).flatMap((group) => group.items.map((item) => item.path))).toContain('/admin/access-control')
  })

  it('matches entity detail deep links with stable id parameters', () => {
    const routeObjects = [...pageRoutes, ...detailRoutes].map((route) => ({ path: route.path }))
    expect(matchRoutes(routeObjects, '/operations/tasks/task-123')?.[0].params.taskId).toBe('task-123')
    expect(matchRoutes(routeObjects, '/operations/runs/run-456')?.[0].params.runId).toBe('run-456')
    expect(matchRoutes(routeObjects, '/admin/models/providers/provider-7')?.[0].params.providerId).toBe('provider-7')
  })
})
