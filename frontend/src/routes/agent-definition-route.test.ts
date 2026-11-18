import { describe, expect, it } from 'vitest'

import {
  detailRoutes,
  getVisibleNavigation,
  isCanonicalRouteSetValid,
  pageRoutes,
} from './route-config'

describe('Agent definition route', () => {
  it('is directly reachable and visible when auth is disabled', () => {
    const route = pageRoutes.find((item) => item.path === '/admin/agents')

    expect(route).toBeDefined()
    expect(route?.owner).toBe('#757')
    expect(route?.authOnly).toBeUndefined()
    expect(
      getVisibleNavigation(false).some((group) =>
        group.items.some((item) => item.path === '/admin/agents'),
      ),
    ).toBe(true)
  })

  it('registers a refreshable Agent definition detail deep link', () => {
    const detail = detailRoutes.find(
      (item) => item.path === '/admin/agents/:agentId',
    )

    expect(detail).toMatchObject({
      pageId: 'ADM-AGENT-DETAIL',
      parentPath: '/admin/agents',
      entityIdParam: 'agentId',
      owner: '#757',
    })
  })

  it('keeps the canonical route set unique', () => {
    expect(isCanonicalRouteSetValid()).toBe(true)
  })
})
