import { describe, expect, it } from 'vitest'
import { matchRoutes } from 'react-router-dom'

import {
  authRoutes,
  detailRoutes,
  getAllRoutes,
  getVisibleNavigation,
  getWorkspaceViewMode,
  globalRoutes,
  isCanonicalRouteSetValid,
  pageRoutes,
} from './route-config'

describe('route configuration', () => {
  it('maps workspace and nested office paths to the correct view mode', () => {
    expect(getWorkspaceViewMode('/workspace/overview')).toBe('home')
    expect(getWorkspaceViewMode('/workspace/office')).toBe('office')
    expect(getWorkspaceViewMode('/workspace/office/floor/2')).toBe('office')
    expect(getWorkspaceViewMode('/operations/agents')).toBe('home')
  })

  it('keeps canonical page ids and paths unique', () => {
    expect(
      isCanonicalRouteSetValid([...pageRoutes, ...globalRoutes, ...authRoutes]),
    ).toBe(true)
  })

  it('provides six primary navigation items with system administration grouped under workflow', () => {
    expect(getVisibleNavigation(false).map((group) => group.id)).toEqual([
      'home',
      'office',
      'tasks',
      'collaboration',
      'workflow',
      'cost',
    ])
    expect(getVisibleNavigation(false).find((group) => group.id === 'workflow')?.label)
      .toBe('工作流与系统管理')
    expect(
      getVisibleNavigation(false).find((group) => group.id === 'workflow')?.subpages,
    ).toEqual(expect.arrayContaining([
      expect.objectContaining({ path: '/admin/workflows' }),
      expect.objectContaining({ path: '/admin/agents' }),
      expect.objectContaining({ path: '/admin/settings' }),
    ]))
  })

  it('uses the frozen dashboard route as the default route target', () => {
    expect(
      getAllRoutes(false).find((route) => route.pageId === 'WS-OVERVIEW')?.path,
    ).toBe('/workspace/overview')
  })

  it('keeps Agent collaboration reachable in no-auth mode', () => {
    const visiblePaths = getVisibleNavigation(false).flatMap((group) =>
      group.items.map((item) => item.path),
    )
    expect(visiblePaths).toContain('/workspace/collaboration')
    expect(
      getAllRoutes(false).find((route) => route.pageId === 'WS-COLLAB')?.path,
    ).toBe('/workspace/collaboration')
  })

  it('does not expose identity and RBAC navigation in no-auth mode', () => {
    const paths = getVisibleNavigation(false).flatMap((group) =>
      group.items.map((item) => item.path),
    )
    expect(paths).not.toContain('/admin/organization')
    expect(paths).not.toContain('/admin/access-control')
    expect(paths.every((path) => !path.includes('/:'))).toBe(true)
    expect(getAllRoutes(false)).not.toEqual(expect.arrayContaining(authRoutes))
  })

  it('keeps Workflow designer routes directly navigable in no-auth mode', () => {
    const paths = getVisibleNavigation(false).flatMap((group) =>
      group.items.map((item) => item.path),
    )
    expect(paths).toContain('/admin/workflows')
    const routes = getAllRoutes(false)
    expect(routes.find((route) => route.pageId === 'ADM-WORKFLOWS')?.owner).toBe('#758')
    expect(
      matchRoutes(
        routes.map((route) => ({ path: route.path })),
        '/admin/workflows/research-flow/edit',
      )?.[0].params.workflowId,
    ).toBe('research-flow')
    expect(
      matchRoutes(
        routes.map((route) => ({ path: route.path })),
        '/admin/workflows/research-flow',
      )?.[0].params.workflowId,
    ).toBe('research-flow')
  })

  it('shows capability-gated navigation only when bootstrap advertises it', () => {
    const withoutCapability = getVisibleNavigation(true).flatMap((group) =>
      group.items.map((item) => item.path),
    )
    const withCapability = getVisibleNavigation(true, [
      'prompt_management',
    ]).flatMap((group) => group.items.map((item) => item.path))
    expect(withoutCapability).not.toContain('/admin/prompts')
    expect(withCapability).toContain('/admin/prompts')
  })

  it('registers Prompt Registry detail deep links under #807', () => {
    const routeObjects = [...pageRoutes, ...detailRoutes].map((route) => ({
      path: route.path,
    }))
    expect(
      matchRoutes(routeObjects, '/admin/prompts/customer-support')?.[0].params
        .promptId,
    ).toBe('customer-support')
    expect(
      detailRoutes.find((route) => route.path === '/admin/prompts/:promptId')
        ?.owner,
    ).toBe('#807')
    expect(
      detailRoutes.find((route) => route.path === '/admin/prompts/:promptId')
        ?.capability,
    ).toBe('prompt_management')
  })

  it('exposes identity and RBAC routes only when server mode enables auth', () => {
    expect(getAllRoutes(true)).toEqual(expect.arrayContaining(authRoutes))
    expect(
      getVisibleNavigation(true).flatMap((group) =>
        group.items.map((item) => item.path),
      ),
    ).toContain('/admin/access-control')
  })

  it('gates Agent runtime list/detail on registry capability and assigns #808 ownership', () => {
    const visibleWithoutRegistry = getVisibleNavigation(true).flatMap((group) => group.items.map((item) => item.path))
    const visibleWithRegistry = getVisibleNavigation(true, ['agent_management']).flatMap((group) => group.items.map((item) => item.path))
    expect(visibleWithoutRegistry).not.toContain('/operations/agents')
    expect(visibleWithRegistry).toContain('/operations/agents')
    expect(pageRoutes.find((route) => route.path === '/operations/agents')?.owner).toBe('#808')
    expect(detailRoutes.find((route) => route.path === '/operations/agents/:agentId')?.owner).toBe('#808')
    expect(matchRoutes([...pageRoutes, ...detailRoutes].map((route) => ({ path: route.path })), '/operations/agents/agent-123')?.[0].params.agentId).toBe('agent-123')
  })

  it('registers Knowledge collection and document deep links under #760', () => {
    const routeObjects = [...pageRoutes, ...detailRoutes].map((route) => ({ path: route.path }))
    expect(matchRoutes(routeObjects, '/operations/knowledge-bases/collection-123')?.[0].params.collectionId).toBe('collection-123')
    expect(matchRoutes(routeObjects, '/operations/documents/document-456')?.[0].params.documentId).toBe('document-456')
    expect(detailRoutes.find((route) => route.path === '/operations/knowledge-bases/:collectionId')?.owner).toBe('#760')
  })

  it('registers Skills registry list and detail routes under #806', () => {
    const list = pageRoutes.find((route) => route.path === '/admin/skills')
    const detail = detailRoutes.find((route) => route.path === '/admin/skills/:skillId')
    expect(list?.owner).toBe('#806')
    expect(detail?.owner).toBe('#806')
    expect(matchRoutes([...pageRoutes, ...detailRoutes].map((route) => ({ path: route.path })), '/admin/skills/skill-123')?.[0].params.skillId).toBe('skill-123')
  })

  it('matches entity detail deep links with stable id parameters', () => {
    const routeObjects = [...pageRoutes, ...detailRoutes].map((route) => ({
      path: route.path,
    }))
    expect(
      matchRoutes(routeObjects, '/operations/tasks/task-123')?.[0].params
        .taskId,
    ).toBe('task-123')
    expect(
      matchRoutes(routeObjects, '/operations/runs/run-456')?.[0].params.runId,
    ).toBe('run-456')
    expect(
      matchRoutes(routeObjects, '/admin/models/providers/provider-7')?.[0]
        .params.providerId,
    ).toBe('provider-7')
  })
})
