import { describe, expect, it } from 'vitest'
import { matchRoutes } from 'react-router-dom'

import {
  authRoutes,
  detailRoutes,
  getAllRoutes,
  getVisibleNavigation,
  globalRoutes,
  isCanonicalRouteSetValid,
  pageRoutes,
} from '@/routes/route-config'

const allCanonicalRoutes = [...pageRoutes, ...detailRoutes, ...globalRoutes, ...authRoutes]
const routeObjects = allCanonicalRoutes.map((route) => ({ path: route.path }))

describe('Issue #752 system integration gate: route contract', () => {
  it('keeps page ids and canonical paths unique across all route classes', () => {
    expect(isCanonicalRouteSetValid([...pageRoutes, ...globalRoutes, ...authRoutes])).toBe(true)
  })

  it('registers all cross-module journey entry points', () => {
    const paths = new Set(allCanonicalRoutes.map((route) => route.path))
    for (const path of [
      '/workspace/overview', '/workspace/tasks', '/operations/tasks/:taskId',
      '/operations/runs/:runId', '/workspace/collaboration',
      '/operations/workflow-runs/:runId', '/admin/agents', '/admin/workflows',
      '/admin/models/templates', '/workspace/quality-cost', '/operations/approvals',
      '/operations/knowledge-bases', '/operations/documents/:documentId',
      '/workspace/office', '/operations/conversations', '/operations/files',
    ]) {
      expect(paths, `missing canonical route: ${path}`).toContain(path)
    }
  })

  it('registers global search, notifications, audit and operations pages', () => {
    expect(globalRoutes.map((route) => route.path)).toEqual(expect.arrayContaining([
      '/search', '/notifications', '/admin/audit', '/admin/operations',
    ]))
  })

  it('keeps business navigation available without auth and hides identity-only navigation', () => {
    const visible = getVisibleNavigation(false).flatMap((group) => group.items.map((item) => item.path))
    expect(visible).toContain('/workspace/overview')
    expect(visible).toContain('/workspace/tasks')
    expect(visible).toContain('/workspace/office')
    expect(visible).not.toContain('/admin/organization')
    expect(visible).not.toContain('/admin/access-control')
    expect(getAllRoutes(false)).not.toEqual(expect.arrayContaining(authRoutes))
  })

  it('exposes identity navigation only in auth-enabled mode', () => {
    const visible = getVisibleNavigation(true).flatMap((group) => group.items.map((item) => item.path))
    expect(visible).toContain('/admin/organization')
    expect(visible).toContain('/admin/access-control')
    expect(getAllRoutes(true)).toEqual(expect.arrayContaining(authRoutes))
  })

  it.each([
    ['/operations/tasks/task-752', 'taskId', 'task-752'],
    ['/operations/runs/run-752', 'runId', 'run-752'],
    ['/operations/approvals/approval-752', 'approvalId', 'approval-752'],
    ['/operations/documents/doc-752', 'documentId', 'doc-752'],
    ['/operations/conversations/session-752', 'conversationId', 'session-752'],
    ['/operations/files/file-752', 'fileId', 'file-752'],
    ['/operations/workflow-runs/run-752', 'runId', 'run-752'],
    ['/admin/agents/agent-752', 'agentId', 'agent-752'],
    ['/admin/workflows/flow-752/edit', 'workflowId', 'flow-752'],
  ])('resolves direct deep link %s', (url, parameter, expected) => {
    const matches = matchRoutes(routeObjects, url)
    expect(matches?.[0].params[parameter]).toBe(expected)
  })

  it('retains route ownership metadata for module accountability', () => {
    expect(allCanonicalRoutes.every((route) => /^#\d+$/.test(route.owner))).toBe(true)
  })
})