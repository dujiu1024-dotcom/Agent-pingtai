import { describe, expect, it } from 'vitest'
import { normalizeOfficeAgents } from './OfficeWorkspace'

describe('normalizeOfficeAgents', () => {
  it('preserves live status, floor and workstation coordinates from the Office snapshot', () => {
    const agents = normalizeOfficeAgents({
      agents: [{
        agent_id: 'agent-1',
        name: '代码助手',
        status: 'running',
        floor_id: 'floor-02',
        desk_id: 'desk-03',
        current_task: { task_id: 'task-9', title: '代码审查' },
      }],
      runs: [],
    })
    expect(agents[0]).toMatchObject({
      agent_id: 'agent-1',
      name: '代码助手',
      status: 'running',
      floor_id: 'floor-02',
      desk_id: 'desk-03',
      task: '代码审查',
      x: -2.5,
      z: -4.6,
    })
  })

  it('uses actual run status and never fabricates a running state', () => {
    const agents = normalizeOfficeAgents({
      agents: [{ agent_id: 'agent-2', name: '分析助手' }, { agent_id: 'agent-3', name: '离线助手' }],
      runs: [
        { run_id: 'run-2', agent_id: 'agent-2', status: 'waiting_approval', task_id: 'task-2', updated_at: '2026-11-22T09:00:00Z' },
      ],
    })
    expect(agents.map((agent) => agent.status)).toEqual(['waiting_approval', 'offline'])
  })

  it('maps error-bearing run history to failed and successful completed history to idle', () => {
    const agents = normalizeOfficeAgents({
      agents: [{ agent_id: 'agent-fail' }, { agent_id: 'agent-ok' }],
      runs: [
        { run_id: 'run-fail', agent_id: 'agent-fail', ok: false, error: 'timeout' },
        { run_id: 'run-ok', agent_id: 'agent-ok', ok: true },
      ],
    })
    expect(agents.map((agent) => agent.status)).toEqual(['failed', 'idle'])
  })
})
