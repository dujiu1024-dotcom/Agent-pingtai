import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

type Agent = Record<string, unknown>
const obj = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
const label = (value: unknown, fallback: string) =>
  typeof value === 'string' && value.trim() ? value.trim() : fallback

export function agentTeamLabel(agent: Agent): string {
  const definition = obj(agent.definition)
  return label(definition.team_name ?? definition.team ?? definition.department_name ?? definition.department, '未分组')
}

export function agentRoleLabel(agent: Agent): string {
  return label(obj(agent.definition).role, '未分配角色')
}

/** Only filter on Registry-declared fields; never infer membership or permissions. */
export function filterAgentTeamMembers(agents: Agent[], team: string, role: string, search: string): Agent[] {
  const query = search.trim().toLocaleLowerCase()
  return agents.filter((agent) => {
    const definition = obj(agent.definition)
    const name = label(definition.name, label(agent.item_id, '')).toLocaleLowerCase()
    const id = label(agent.item_id, '').toLocaleLowerCase()
    return (team === 'all' || agentTeamLabel(agent) === team) &&
      (role === 'all' || agentRoleLabel(agent) === role) &&
      (!query || name.includes(query) || id.includes(query))
  })
}

/** Grouping uses declared team/department labels only; no inferred collaboration edges. */
export function AgentTeamGraph({ agents }: { agents: Agent[] }) {
  const [view, setView] = useState<'graph' | 'list'>('graph')
  const [team, setTeam] = useState('all')
  const [role, setRole] = useState('all')
  const [search, setSearch] = useState('')
  const teams = useMemo(() => [...new Set(agents.map(agentTeamLabel))].sort(), [agents])
  const roles = useMemo(() => [...new Set(agents.map(agentRoleLabel))].sort(), [agents])
  const filtered = useMemo(() => filterAgentTeamMembers(agents, team, role, search), [agents, team, role, search])
  const groups = useMemo(() => {
    const grouped = new Map<string, Agent[]>()
    for (const agent of filtered) {
      const group = agentTeamLabel(agent)
      grouped.set(group, [...(grouped.get(group) ?? []), agent])
    }
    return [...grouped.entries()]
  }, [filtered])

  return (
    <section className="agent-team-graph" aria-label="Agent 团队与成员角色管理">
      <header>
        <div>
          <h3>团队与成员角色</h3>
          <p>团队、部门和角色来自 Agent Registry 当前生效定义；未声明字段不推断成员关系或权限</p>
        </div>
        <div role="group" aria-label="团队视图">
          <button type="button" aria-pressed={view === 'graph'} onClick={() => setView('graph')}>团队视图</button>
          <button type="button" aria-pressed={view === 'list'} onClick={() => setView('list')}>成员列表</button>
        </div>
      </header>
      <div className="agent-team-controls" aria-label="团队成员筛选">
        <label>团队
          <select aria-label="按团队筛选" value={team} onChange={(event) => setTeam(event.target.value)}>
            <option value="all">全部团队</option>
            {teams.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <label>成员角色
          <select aria-label="按角色筛选" value={role} onChange={(event) => setRole(event.target.value)}>
            <option value="all">全部角色</option>
            {roles.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <label className="agent-team-search">搜索成员
          <input aria-label="搜索成员名称或标识" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="名称或 Agent ID" />
        </label>
        <span className="agent-team-result-count">显示 {filtered.length} / {agents.length} 名成员</span>
      </div>
      <p className="agent-team-role-note">
        角色来源：Agent Registry definition.role。当前后端未提供团队成员角色专用写入 API；此页不创建虚构角色绑定、不直接改写数据库。Agent 定义的版本编辑仍由 Agent Registry 页面负责。
      </p>
      {filtered.length === 0 ? (
        <p className="agent-team-empty" role="status">没有符合当前团队、角色和搜索条件的成员。</p>
      ) : view === 'graph' ? (
        <div className="agent-team-groups">
          {groups.map(([group, members]) => (
            <section key={group} className="agent-team-group">
              <h4>{group}<span>{members.length}</span></h4>
              <div className="agent-team-members">
                {members.map((agent, index) => {
                  const definition = obj(agent.definition)
                  const id = label(agent.item_id, String(index))
                  const name = label(definition.name, id)
                  return (
                    <Link key={id} to={`/admin/agents/${encodeURIComponent(id)}`} className="agent-team-node">
                      <span>{name.slice(0, 1).toUpperCase()}</span>
                      <strong>{name}</strong>
                      <small>角色：{agentRoleLabel(agent)}</small>
                      <small>模型：{label(definition.model_template_name ?? definition.model_template_id, '未声明')}</small>
                      <small>状态：{label(agent.health, label(agent.status, '未知'))}</small>
                      <small>运行中：{typeof agent.active_run_count === 'number' ? agent.active_run_count : '未提供'}</small>
                      <small className="agent-team-member-id">{id}</small>
                    </Link>
                  )
                })}
              </div>
            </section>
          ))}
        </div>
      ) : (
        <div className="generic-table-wrap">
          <table className="generic-table agent-team-member-table">
            <thead><tr><th>成员</th><th>Agent ID</th><th>团队</th><th>角色</th><th>状态</th><th>运行中</th><th>定义</th></tr></thead>
            <tbody>
              {filtered.map((agent, index) => {
                const definition = obj(agent.definition)
                const id = label(agent.item_id, String(index))
                return (
                  <tr key={id}>
                    <td>{label(definition.name, id)}</td>
                    <td><code>{id}</code></td>
                    <td>{agentTeamLabel(agent)}</td>
                    <td><span className="agent-team-role-pill">{agentRoleLabel(agent)}</span></td>
                    <td>{label(agent.health, label(agent.status, '未知'))}</td>
                    <td>{typeof agent.active_run_count === 'number' ? agent.active_run_count : '未提供'}</td>
                    <td><Link to={`/admin/agents/${encodeURIComponent(id)}`}>查看定义</Link></td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
