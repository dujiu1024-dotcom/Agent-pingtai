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


describe('Agent runtime routes', () => {
  it('assigns the runtime list and detail routes to #808', () => {
    const list = pageRoutes.find((item) => item.path === '/operations/agents')
    const detail = pageRoutes.find((item) => item.path === '/operations/agents/:agentId')

    expect(list?.owner).toBe('#808')
    expect(detail).toMatchObject({
      pageId: 'OPS-AGENT-DETAIL',
      parentPath: '/operations/agents',
      entityIdParam: 'agentId',
      owner: '#808',
    })
  })
})
