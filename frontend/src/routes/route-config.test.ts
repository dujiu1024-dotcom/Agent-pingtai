import { describe, expect, it } from 'vitest'
import { matchRoutes } from 'react-router-dom'

import {
  authRoutes,
  detailRoutes,
  getAllRoutes,
  getNavigationSections,
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
    expect(getNavigationSections(false).map((group) => group.id)).toEqual([
      'home',
      'office',
      'tasks',
      'collaboration',
      'workflow',
      'cost',
    ])
    expect(getNavigationSections(false).find((group) => group.id === 'workflow')?.label)
      .toBe('工作流与系统管理')
    expect(
      getNavigationSections(false).find((group) => group.id === 'workflow')?.subpages,
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

  it('registers the Operations dashboard route and resolves direct navigation', () => {
    const dashboard = pageRoutes.find(
      (route) => route.path === '/operations/dashboard',
    )
    expect(dashboard?.pageId).toBe('OPS-DASHBOARD')
    expect(dashboard?.owner).toBe('#797')
    expect(
      matchRoutes(
        [...pageRoutes, ...detailRoutes, ...globalRoutes].map((route) => ({
          path: route.path,
        })),
        '/operations/dashboard',
      ),
    ).not.toBeNull()
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

  it('registers organization and canonical RBAC pages as auth-only routes', () => {
    const organization = pageRoutes.find(
      (route) => route.path === '/admin/organization',
    )
    const rbac = pageRoutes.find((route) => route.path === '/admin/rbac')
    const legacyRbac = pageRoutes.find(
      (route) => route.path === '/admin/access-control',
    )

    expect(organization?.authOnly).toBe(true)
    expect(organization?.owner).toBe('#798')
    expect(rbac?.pageId).toBe('ADM-RBAC')
    expect(rbac?.authOnly).toBe(true)
    expect(rbac?.owner).toBe('#798')
    expect(legacyRbac?.authOnly).toBe(true)

    const routeObjects = getAllRoutes(true).map((route) => ({
      path: route.path,
    }))
    expect(matchRoutes(routeObjects, '/admin/organization')).not.toBeNull()
    expect(matchRoutes(routeObjects, '/admin/rbac')).not.toBeNull()
    expect(matchRoutes(routeObjects, '/admin/access-control')).not.toBeNull()
  })

  it('uses /admin/rbac for RBAC navigation and hides it without auth', () => {
    const authPaths = getVisibleNavigation(true).flatMap((group) =>
      group.items.map((item) => item.path),
    )
    const noAuthPaths = getVisibleNavigation(false).flatMap((group) =>
      group.items.map((item) => item.path),
    )

    expect(authPaths).toContain('/admin/rbac')
    expect(authPaths).not.toContain('/admin/access-control')
    expect(noAuthPaths).not.toContain('/admin/rbac')
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

  it('assigns login and administrator initialization routes to #809', () => {
    expect(authRoutes.map((route) => route.path)).toEqual([
      '/auth/login',
      '/auth/initialize',
    ])
    expect(authRoutes.every((route) => route.authOnly && route.owner === '#809')).toBe(true)
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

  it('registers file asset routes under #802 and preserves deep-link ids', () => {
    const filesRoute = pageRoutes.find((route) => route.path === '/operations/files')
    const fileDetailRoute = detailRoutes.find((route) => route.path === '/operations/files/:fileId')
    expect(filesRoute?.owner).toBe('#802')
    expect(fileDetailRoute?.owner).toBe('#802')
    expect(
      matchRoutes([...pageRoutes, ...detailRoutes].map((route) => ({ path: route.path })), '/operations/files/asset-123')?.[0].params.fileId,
    ).toBe('asset-123')
  })
})


describe('model management routes', () => {
  it('assigns all Provider, Template and Routing list/detail routes to #804', () => {
    const expected = [
      ['/admin/models/providers', 'ADM-MODEL-PROVIDERS'],
      ['/admin/models/templates', 'ADM-MODEL-TEMPLATES'],
      ['/admin/models/routing', 'ADM-MODEL-ROUTING'],
      ['/admin/models/providers/:providerId', 'ADM-MODEL-PROVIDERS'],
      ['/admin/models/templates/:templateId', 'ADM-MODEL-TEMPLATES'],
      ['/admin/models/routing/:policyId', 'ADM-MODEL-ROUTING'],
    ]
    for (const [path, pageId] of expected) {
      const route = [...pageRoutes, ...detailRoutes].find((item) => item.path === path)
      expect(route?.pageId).toBe(pageId)
      expect(route?.owner).toBe('#804')
    }
  })
})
