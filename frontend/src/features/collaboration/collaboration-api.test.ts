import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn() },
}))

import { apiClient } from '@/lib/api'
import {
  fetchCollaborationAgents,
  fetchCollaborationRun,
  fetchCollaborationRunEvents,
  fetchRecentCollaborationRuns,
} from './collaboration-api'

const get = vi.mocked(apiClient.get)

describe('collaboration API adapters', () => {
  beforeEach(() => {
    get.mockReset()
    get.mockResolvedValue({})
  })

  it('loads the live Agent Registry directory without inventing a team endpoint', async () => {
    await fetchCollaborationAgents()
    expect(get).toHaveBeenCalledWith('/platform/v1/registry/agent')
  })

  it('loads recent workflow Runs from the shared Run API', async () => {
    await fetchRecentCollaborationRuns()
    expect(get).toHaveBeenCalledWith('/platform/v1/runs?limit=10')
  })

  it('encodes Run identifiers before requesting the Run snapshot', async () => {
    await fetchCollaborationRun('team/run 1')
    expect(get).toHaveBeenCalledWith('/platform/v1/runs/team%2Frun%201')
  })

  it('loads node and lifecycle events for the selected Run', async () => {
    await fetchCollaborationRunEvents('run-123')
    expect(get).toHaveBeenCalledWith('/platform/v1/runs/run-123/events?limit=200')
  })
})
