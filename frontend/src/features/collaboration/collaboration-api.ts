import { apiClient } from '@/lib/api'

/** Read-only collaboration data adapters; authorization remains enforced by the API. */
export const fetchCollaborationAgents = () =>
  apiClient.get<unknown>('/platform/v1/registry/agent')

export const fetchRecentCollaborationRuns = () =>
  apiClient.get<unknown>('/platform/v1/runs?limit=10')

export const fetchCollaborationRun = (runId: string) =>
  apiClient.get<Record<string, unknown>>(
    `/platform/v1/runs/${encodeURIComponent(runId.trim())}`,
  )

export const fetchCollaborationRunEvents = (runId: string) =>
  apiClient.get<unknown>(
    `/platform/v1/runs/${encodeURIComponent(runId.trim())}/events?limit=200`,
  )
