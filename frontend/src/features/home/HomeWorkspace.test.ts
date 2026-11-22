import { describe, expect, it } from 'vitest'
import { buildHomeActivityFeed, filterHomeTasks, rankHomeAgents } from './HomeWorkspace'
import type { TaskRecord } from '@/features/tasks/task-model'

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

  it('ranks Agents by distinct Run IDs from task API records', () => {
    const agents = [
      { item_id: 'agent-a', definition: { name: 'A' } },
      { item_id: 'agent-b', definition: { name: 'B' } },
    ]
    const taskRows = [
      { task_id: 't1', agent_id: 'agent-b', run_id: 'r1' },
      { task_id: 't2', agent_id: 'agent-b', run_id: 'r1' },
      { task_id: 't3', agent_id: 'agent-b', run_id: 'r2' },
      { task_id: 't4', agent_id: 'agent-a' },
    ] as TaskRecord[]
    expect(rankHomeAgents(agents, taskRows).map((agent) => [agent.id, agent.runs])).toEqual([['agent-b', 2], ['agent-a', 0]])
    expect(rankHomeAgents(agents, []).every((agent) => agent.runs === 0)).toBe(true)
  })

  it('builds Agent, Workflow, completed task and pending approval events from API records', () => {
    const feed = buildHomeActivityFeed(
      [{ task_id: 't-done', status: 'succeeded', finished_at: '2026-11-22T10:00:00Z', input: { title: '发布报告' } }] as TaskRecord[],
      [{ item_id: 'agent-a', definition: { name: '代码助手' }, published_at: '2026-11-22T09:00:00Z' }],
      [{ item_id: 'flow-a', definition: { name: '日报流程' }, created_at: '2026-11-22T08:00:00Z' }],
      [{ approval_id: 'ap-1', action: 'publish', resource: 'report', status: 'pending' }],
    )
    expect(feed.map((event) => event.title)).toEqual(['任务完成', 'Agent 更新', 'Workflow 新增', '审批已提交'])
    expect(feed[3].at).toBeNull()
  })
})
