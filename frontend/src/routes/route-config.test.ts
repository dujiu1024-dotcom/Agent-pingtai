import { describe, expect, it } from 'vitest'
import { matchRoutes } from 'react-router-dom'

import {
  authRoutes,
  detailRoutes,
  getAllRoutes,
  getVisibleNavigation,
  globalRoutes,
  isCanonicalRouteSetValid,
  navigationSections,
  pageRoutes,
  getNavigationSections,
  getRouteBreadcrumbs,
} from './route-config'

describe('route configuration', () => {
  it('keeps canonical page ids and paths unique', () => {
    expect(
      isCanonicalRouteSetValid([...pageRoutes, ...globalRoutes, ...authRoutes]),
    ).toBe(true)
  })

  it('integrates all frontend navigation entries with unique registered routes', () => {
    const routes = getAllRoutes(true)
    const paths = routes.map((route) => route.path)
    const registeredPaths = new Set(paths)

    expect(paths).toHaveLength(new Set(paths).size)
    for (const section of navigationSections) {
      expect(registeredPaths.has(section.path), section.path).toBe(true)
      for (const subpage of section.subpages) {
        expect(registeredPaths.has(subpage.path), subpage.path).toBe(true)
      }
    }

    for (const path of [
      '/workspace/overview',
      '/workspace/tasks',
      '/workspace/collaboration',
      '/workspace/quality-cost',
      '/workspace/office',
      '/admin/agents',
      '/admin/workflows',
      '/admin/tools',
      '/admin/skills',
      '/admin/settings',
      '/admin/audit',
      '/admin/ops',
    ]) {
      expect(registeredPaths.has(path), path).toBe(true)
    }
  })

  it('provides exactly four primary navigation sections', () => {
    expect(getVisibleNavigation(false).map((group) => group.id)).toEqual([
      'workspace',
      'operations',
      'administration',
      'global',
    ])
  })

  it('uses the frozen dashboard route as the default route target', () => {
    expect(
      getAllRoutes(false).find((route) => route.pageId === 'WS-OVERVIEW')?.path,
    ).toBe('/workspace/overview')
  })

  it('registers home and office as the two prototype main views', () => {
    const mainPaths = pageRoutes.map((route) => route.path)
    expect(mainPaths).toContain('/workspace/overview')
    expect(mainPaths).toContain('/workspace/office')
    expect(mainPaths.indexOf('/workspace/overview')).toBeLessThan(
      mainPaths.indexOf('/workspace/office'),
    )
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

  it('exposes identity and RBAC routes only when server mode enables auth', () => {
    expect(getAllRoutes(true)).toEqual(expect.arrayContaining(authRoutes))
    expect(
      getVisibleNavigation(true).flatMap((group) =>
        group.items.map((item) => item.path),
      ),
    ).toContain('/admin/access-control')
  })

  it('registers Knowledge collection and document deep links under #760', () => {
    const routeObjects = [...pageRoutes, ...detailRoutes].map((route) => ({ path: route.path }))
    expect(matchRoutes(routeObjects, '/operations/knowledge-bases/collection-123')?.[0].params.collectionId).toBe('collection-123')
    expect(matchRoutes(routeObjects, '/operations/documents/document-456')?.[0].params.documentId).toBe('document-456')
    expect(detailRoutes.find((route) => route.path === '/operations/knowledge-bases/:collectionId')?.owner).toBe('#760')
  })

  it('registers model provider, template and routing routes under #804', () => {
    const routeObjects = [...pageRoutes, ...detailRoutes].map((route) => ({ path: route.path }))
    expect(pageRoutes.find((route) => route.path === '/admin/models/providers')?.owner).toBe('#804')
    expect(pageRoutes.find((route) => route.path === '/admin/models/templates')?.owner).toBe('#804')
    expect(pageRoutes.find((route) => route.path === '/admin/models/routing')?.owner).toBe('#804')
    expect(detailRoutes.find((route) => route.path === '/admin/models/providers/:providerId')?.owner).toBe('#804')
    expect(detailRoutes.find((route) => route.path === '/admin/models/templates/:templateId')?.owner).toBe('#804')
    expect(matchRoutes(routeObjects, '/admin/models/providers/provider-7')?.[0].params.providerId).toBe('provider-7')
    expect(matchRoutes(routeObjects, '/admin/models/templates/template-9')?.[0].params.templateId).toBe('template-9')
    expect(matchRoutes(routeObjects, '/admin/models/routing/policy-1')?.[0].params.policyId).toBe('policy-1')
  })

  it('registers system settings as an authenticated read-only route owned by #812', () => {
    const settings = pageRoutes.find((route) => route.path === '/admin/settings')
    expect(settings?.owner).toBe('#812')
    expect(settings?.authOnly).toBe(true)
    expect(
      getVisibleNavigation(false).flatMap((group) => group.items.map((item) => item.path)),
    ).not.toContain('/admin/settings')
    expect(
      getVisibleNavigation(true).flatMap((group) => group.items.map((item) => item.path)),
    ).toContain('/admin/settings')
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

  it('wires the files workspace route and assigns list/detail ownership to #802', () => {
    const routeObjects = [...pageRoutes, ...detailRoutes].map((route) => ({ path: route.path }))
    const filesRoute = pageRoutes.find((route) => route.path === '/operations/files')
    const fileDetailRoute = detailRoutes.find((route) => route.path === '/operations/files/:fileId')

    expect(filesRoute?.owner).toBe('#802')
    expect(filesRoute?.authOnly).toBeUndefined()
    expect(fileDetailRoute?.owner).toBe('#802')
    expect(fileDetailRoute?.authOnly).toBeUndefined()
    expect(
      matchRoutes(routeObjects, '/operations/files/asset-123')?.[0].params.fileId,
    ).toBe('asset-123')
  })
  it('registers Skills list and detail routes under #806', () => {
    const routes = [...pageRoutes, ...detailRoutes]
    const list = routes.find((route) => route.path === '/admin/skills')
    const detail = routes.find((route) => route.path === '/admin/skills/:skillId')
    expect(list?.owner).toBe('#806')
    expect(detail?.owner).toBe('#806')
    expect(matchRoutes(routes.map((route) => ({ path: route.path })), '/admin/skills/skill-123')?.[0].params.skillId).toBe('skill-123')
  })

  it('connects all Evaluation workspace routes to the #803 main page', () => {
    const routeObjects = [...pageRoutes, ...detailRoutes].map((route) => ({ path: route.path }))
    const expected = [
      ['/operations/evaluation/datasets', '#803'],
      ['/operations/evaluation/datasets/:datasetId', '#803'],
      ['/operations/evaluation/runs', '#803'],
      ['/operations/evaluation/runs/:evaluationRunId', '#803'],
    ] as const

    for (const [path, owner] of expected) {
      const route = [...pageRoutes, ...detailRoutes].find((item) => item.path === path)
      expect(route?.owner).toBe(owner)
    }
    expect(matchRoutes(routeObjects, '/operations/evaluation/datasets/dataset-123')?.[0].params.datasetId).toBe('dataset-123')
    expect(matchRoutes(routeObjects, '/operations/evaluation/runs/run-456')?.[0].params.evaluationRunId).toBe('run-456')
  })

})


describe('system operations dashboard route', () => {
  it('registers the Metrics, Health and Scheduler dashboard under Issue #813', () => {
    const route = globalRoutes.find((item) => item.path === '/admin/ops')
    expect(route).toMatchObject({
      pageId: 'GLOBAL-OPS',
      title: '系统运维',
      owner: '#813',
    })
    expect(route?.description).toContain('Metrics')
  })
})

describe('authentication entry routes', () => {
  it('assigns login and administrator initialization routes to #809', () => {
    expect(authRoutes.map((route) => route.path)).toEqual(['/auth/login', '/auth/initialize'])
    expect(authRoutes.every((route) => route.authOnly && route.owner === '#809')).toBe(true)
  })

  it('registers Tools registry list and detail routes under #805', () => {
    const routeObjects = [...pageRoutes, ...detailRoutes].map((route) => ({ path: route.path }))
    expect(pageRoutes.find((route) => route.path === '/admin/tools')?.owner).toBe('#805')
    expect(detailRoutes.find((route) => route.path === '/admin/tools/:toolId')?.owner).toBe('#805')
    expect(
      matchRoutes(routeObjects, '/admin/tools/mcp-fetch')?.[0].params.toolId,
    ).toBe('mcp-fetch')
  })


})

describe('unified shell navigation and breadcrumbs', () => {
  it('filters auth-only and capability navigation from the canonical sections', () => {
    const noAuth = getNavigationSections(false).flatMap((section) => section.subpages.map((page) => page.path))
    const withAuth = getNavigationSections(true).flatMap((section) => section.subpages.map((page) => page.path))
    expect(noAuth).not.toContain('/admin/organization')
    expect(noAuth).not.toContain('/admin/access-control')
    expect(withAuth).toContain('/admin/organization')
    expect(getNavigationSections(true).flatMap((section) => section.subpages.map((page) => page.path))).not.toContain('/admin/prompts')
    expect(getNavigationSections(true, ['prompt_management']).flatMap((section) => section.subpages.map((page) => page.path))).toContain('/admin/prompts')
  })

  it('builds list and detail breadcrumbs from the canonical route map', () => {
    expect(getRouteBreadcrumbs('/admin/tools').map((item) => item.label)).toEqual(['系统管理', '工具与 MCP'])
    expect(getRouteBreadcrumbs('/admin/tools/tool-123').map((item) => item.label)).toEqual(['系统管理', '工具与 MCP', '工具详情 · tool-123'])
    expect(getRouteBreadcrumbs('/operations/tasks/task-123').map((item) => item.label)).toEqual(['任务中心', '任务指挥中心', '任务详情 · task-123'])
  })

  it('does not expose an unrelated breadcrumb for unknown routes', () => {
    expect(getRouteBreadcrumbs('/not-registered')).toEqual([])
  })
})
