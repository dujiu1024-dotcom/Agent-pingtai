import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

type Agent = Record<string, unknown>
const obj = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
const label = (value: unknown, fallback: string) => typeof value === 'string' && value.trim() ? value : fallback

/** Grouping uses declared team/department labels only; it never infers collaboration edges from shared capabilities. */
export function AgentTeamGraph({ agents }: { agents: Agent[] }) {
  const [view, setView] = useState<'graph' | 'list'>('graph')
  const groups = useMemo(() => {
    const grouped = new Map<string, Agent[]>()
    for (const agent of agents) {
      const definition = obj(agent.definition)
      const group = label(definition.team_name ?? definition.team ?? definition.department_name ?? definition.department, '未分组')
      grouped.set(group, [...(grouped.get(group) ?? []), agent])
    }
    return [...grouped.entries()]
  }, [agents])
  return <section className="agent-team-graph" aria-label="Agent 团队关系视图">
    <header><div><h3>团队关系图</h3><p>按注册定义中声明的 team / department 分组；未声明关系不绘制虚构连线</p></div>
      <div role="group" aria-label="团队视图"><button type="button" aria-pressed={view === 'graph'} onClick={() => setView('graph')}>关系图</button><button type="button" aria-pressed={view === 'list'} onClick={() => setView('list')}>列表</button></div>
    </header>
    {view === 'graph' ? <div className="agent-team-groups">{groups.map(([group, members]) => <section key={group} className="agent-team-group"><h4>{group}<span>{members.length}</span></h4><div className="agent-team-members">{members.map((agent, index) => {
      const definition = obj(agent.definition)
      const id = label(agent.item_id, String(index))
      const name = label(definition.name, id)
      return <Link key={id} to={`/admin/agents/${encodeURIComponent(id)}`} className="agent-team-node"><span>{name.slice(0, 1).toUpperCase()}</span><strong>{name}</strong><small>{label(definition.role, label(agent.kind, 'Agent'))}</small></Link>
    })}</div></section>)}</div> : <ul className="agent-team-list">{agents.map((agent, index) => {
      const definition = obj(agent.definition)
      const id = label(agent.item_id, String(index))
      return <li key={id}><Link to={`/admin/agents/${encodeURIComponent(id)}`}>{label(definition.name, id)}</Link><span>{label(definition.role, label(agent.kind, 'Agent'))}</span><span>{label(definition.team_name ?? definition.team ?? definition.department_name ?? definition.department, '未分组')}</span></li>
    })}</ul>}
  </section>
}
