import { describe, expect, it } from 'vitest'
import { filterHomeTasks, rankHomeAgents, sortRecentRuns } from './HomeWorkspace'
import type { RunRecord, TaskRecord } from '@/features/tasks/task-model'

const tasks = [
  { task_id: 't1', status: 'running' },
  { task_id: 't2', status: 'waiting_approval' },
  { task_id: 't3', status: 'succeeded' },
] as TaskRecord[]
describe('Home workspace data selectors', () => {
  it('filters all, running and approval task tabs from server task states', () => {
    expect(filterHomeTasks(tasks, 'all')).toHaveLength(3)
    expect(filterHomeTasks(tasks, 'running').map((task) => task.task_id)).toEqual(['t1'])
    expect(filterHomeTasks(tasks, 'approval').map((task) => task.task_id)).toEqual(['t2'])
  })
  it('ranks registered agents by observed run records, without inventing counts', () => {
    const agents = [
      { item_id: 'agent-a', definition: { name: 'A' } },
      { item_id: 'agent-b', definition: { name: 'B' } },
    ]
    const runs = [
      { run_id: 'r1', agent_id: 'agent-b', status: 'completed' },
      { run_id: 'r2', agent_id: 'agent-b', status: 'running' },
      { run_id: 'r3', agent_id: 'agent-a', status: 'failed' },
    ] as RunRecord[]
    expect(rankHomeAgents(agents, runs).map((agent) => [agent.id, agent.runs])).toEqual([['agent-b', 2], ['agent-a', 1]])
    expect(rankHomeAgents(agents, []).every((agent) => agent.runs === 0)).toBe(true)
  })
  it('sorts recent runs newest first and limits the timeline', () => {
    const runs = Array.from({ length: 8 }, (_, index) => ({
      run_id: `r${index}`, status: 'completed', updated_at: new Date(2026, 0, index + 1).toISOString(),
    })) as RunRecord[]
    expect(sortRecentRuns(runs)).toHaveLength(6)
    expect(sortRecentRuns(runs)[0].run_id).toBe('r7')
  })
})
