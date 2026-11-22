import { describe, expect, it } from 'vitest'
import {
  agentRoleLabel,
  agentTeamLabel,
  filterAgentTeamMembers,
} from './AgentTeamGraph'

const agents = [
  { item_id: 'researcher-1', definition: { name: 'Researcher', team_name: 'Research', role: 'researcher' } },
  { item_id: 'critic-1', definition: { name: 'Critic', department: 'Research', role: 'reviewer' } },
  { item_id: 'writer-1', definition: { name: 'Writer', team: 'Content', role: 'writer' } },
  { item_id: 'unassigned-1', definition: { name: 'Unassigned' } },
]

describe('Agent team directory adapters', () => {
  it('uses only declared team and role values with explicit fallbacks', () => {
    expect(agentTeamLabel(agents[0])).toBe('Research')
    expect(agentTeamLabel(agents[3])).toBe('未分组')
    expect(agentRoleLabel(agents[1])).toBe('reviewer')
    expect(agentRoleLabel(agents[3])).toBe('未分配角色')
  })

  it('filters members by declared team and role', () => {
    expect(filterAgentTeamMembers(agents, 'Research', 'all', '').map((item) => item.item_id))
      .toEqual(['researcher-1', 'critic-1'])
    expect(filterAgentTeamMembers(agents, 'all', 'reviewer', '').map((item) => item.item_id))
      .toEqual(['critic-1'])
  })

  it('searches member name and identifier case-insensitively', () => {
    expect(filterAgentTeamMembers(agents, 'all', 'all', 'WRITER').map((item) => item.item_id))
      .toEqual(['writer-1'])
    expect(filterAgentTeamMembers(agents, 'all', 'all', 'critic-1').map((item) => item.item_id))
      .toEqual(['critic-1'])
  })
})
