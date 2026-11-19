import { useEffect, useMemo, useState } from 'react'
import { EmptyState, ErrorState } from '@/components/States'
import { apiClient } from '@/lib/api/api-client'
import { OfficeScene3D } from '@/components/OfficeScene3D'
import './office-workspace.css'

type Floor = { id: string; label: string; name: string; description: string }
type Zone = { id: string; label: string; kind: string; description: string }
type AgentStatus = 'idle' | 'running'
type OfficeAgent = { agent_id: string; name: string; role: string; status: AgentStatus }
type OfficePayload = {
  generated_at: string
  metrics: { total: number; open: number; closed: number; done: number; agents: number }
  agents: { name: string; title: string; desc: string; command: string }[]
  llm: { running: string[]; model: string; tokens_today: number }
  tasks: { id: string; title: string; state: string; owner: string; db_status: string }[]
  runs: { id: string; role: string; task_id: string; model: string; ok: boolean; created_at: string }[]
  events: { pending: number; list: { at: string | null; type: string; text: string; task: string }[] }
}
type OfficeEnvelope = { ok: boolean; data: OfficePayload }

const floors: Floor[] = [
  { id: 'floor-03', label: '03', name: '创新与协作层', description: '会议、协作与项目空间' },
  { id: 'floor-02', label: '02', name: '智能体工作层', description: 'Agent 工位与部门区域' },
  { id: 'floor-01', label: '01', name: '运营与知识层', description: '知识中心与运维空间' },
]
const zones: Zone[] = [
  { id: 'open-work', label: '开放工位区', kind: 'workstations', description: 'Agent 工位与工作状态' },
  { id: 'team-studios', label: '部门工作室', kind: 'departments', description: '团队空间分组' },
  { id: 'meeting-hub', label: '协作会议室', kind: 'meetings', description: '协作 Run 与参与者' },
  { id: 'knowledge-center', label: '知识中心', kind: 'knowledge', description: '知识资产与引用入口' },
  { id: 'operations-center', label: '运维中心', kind: 'operations', description: '运行告警与失败任务' },
]

export function OfficeWorkspace() {
  const [floorId, setFloorId] = useState(floors[1].id)
  const [zoneId, setZoneId] = useState<string | null>(null)
  const [view, setView] = useState<'immersive' | '2d'>('immersive')
  const [search, setSearch] = useState('')
  const [snapshot, setSnapshot] = useState<OfficePayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [updatedAt, setUpdatedAt] = useState<string | null>(null)
  const floor = useMemo(() => floors.find((item) => item.id === floorId) ?? floors[1], [floorId])
  const selectedZone = zones.find((zone) => zone.id === zoneId)

  useEffect(() => {
    let active = true
    const load = async () => {
      try {
        const response = await apiClient.get<OfficeEnvelope>('/platform/v1/office/state')
        if (!active) return
        if (!response?.ok || !response.data) throw new Error('Office state response is invalid')
        setSnapshot(response.data)
        setUpdatedAt(response.data.generated_at)
        setError(null)
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : 'Office 状态读取失败')
      } finally {
        if (active) setLoading(false)
      }
    }
    void load()
    const poll = window.setInterval(() => void load(), 15000)
    return () => { active = false; window.clearInterval(poll) }
  }, [])

  const running = snapshot?.llm.running ?? []
  const agents: OfficeAgent[] = (snapshot?.agents ?? []).map(agent => ({
    agent_id: agent.name,
    name: agent.title || agent.name,
    role: agent.desc || agent.command || 'Agent',
    status: running.includes(agent.name) ? 'running' : 'idle',
  }))
  const filteredAgents = agents.filter(agent => `${agent.name} ${agent.agent_id} ${agent.role}`.toLowerCase().includes(search.toLowerCase()))

  function selectFloor(id: string) {
    setFloorId(id)
    setZoneId(null)
  }

  return (
    <div className="r3-office">
      <header className="r3-office-toolbar">
        <div className="r3-floor-switch" role="group" aria-label="楼层导航">
          {floors.map(item => <button key={item.id} className={item.id === floorId ? 'selected' : ''} onClick={() => selectFloor(item.id)} aria-pressed={item.id === floorId}><b>{item.label}</b><span>{item.name}</span></button>)}
        </div>
        <div className="r3-office-view-switch" role="group" aria-label="办公室视图">
          <button className={view === 'immersive' ? 'selected' : ''} onClick={() => setView('immersive')} aria-pressed={view === 'immersive'}>◈ 沉浸空间</button>
          <button className={view === '2d' ? 'selected' : ''} onClick={() => setView('2d')} aria-pressed={view === '2d'}>▦ 2D 导览</button>
        </div>
      </header>

      <section className="r3-office-overview">
        <div><span className="r3-office-eyebrow">DIGITAL OFFICE / FLOOR {floor.label}</span><h2>{floor.name}</h2><p>{floor.description}</p></div>
        <div className="r3-office-stats"><span><i className={error ? 'r3-live-dot is-error' : 'r3-live-dot'}/>{error ? '状态连接异常' : '状态快照已连接'}</span><b>{agents.length.toString().padStart(2, '0')} <small>Agent</small></b><small>{updatedAt ? `更新于 ${new Date(updatedAt).toLocaleTimeString()}` : '等待首个快照'}</small></div>
      </section>

      <div className="r3-office-grid">
        <section className={view === 'immersive' ? 'r3-scene-panel' : 'r3-scene-panel is-2d'}>
          <div className="r3-scene-panel-head"><div><b>空间总览</b><span>INTERACTIVE 3D ENVIRONMENT</span></div><div className="r3-scene-key"><i/>空间区域 <i className="agent-key"/>Agent 状态</div></div>
          {view === 'immersive' ? <OfficeScene3D zones={zones} selectedZone={zoneId} onSelectZone={id => setZoneId(current => current === id ? null : id)} agents={agents} /> : (
            <div className="r3-office-2d-map">{zones.map((zone,index)=><button key={zone.id} className={zoneId===zone.id?'active':''} onClick={()=>setZoneId(current=>current===zone.id?null:zone.id)}><span>{['▦','⌂','◫','▤','⌘'][index]}</span><b>{zone.label}</b><small>{zone.description}</small><em>{zone.id==='open-work'?agents.length:0} Agent</em></button>)}</div>
          )}
          <footer className="r3-scene-footer"><span>拖动旋转 · 滚轮缩放 · 点击区域查看详情</span><span>楼层布局为默认空间模板</span></footer>
        </section>

        <aside className="r3-office-inspector">
          <div className="r3-inspector-title"><div><span className="r3-office-eyebrow">SPACE EXPLORER</span><h3>空间与 Agent</h3></div><b>{agents.length} <small>AGENTS</small></b></div>
          <label className="r3-office-search"><span>⌕</span><input value={search} onChange={event=>setSearch(event.target.value)} placeholder="搜索 Agent" aria-label="搜索 Agent"/></label>
          <div className="r3-zone-filters">
            <button className={zoneId===null?'active':''} onClick={()=>setZoneId(null)}>全部空间 <b>{agents.length}</b></button>
            {zones.map(zone=><button key={zone.id} className={zoneId===zone.id?'active':''} onClick={()=>setZoneId(current=>current===zone.id?null:zone.id)}>{zone.label}<b>{zone.id==='open-work'?agents.length:0}</b></button>)}
          </div>
          {selectedZone && <div className="r3-zone-detail"><b>{selectedZone.label}</b><p>{selectedZone.description}</p><small>{selectedZone.kind} · {selectedZone.id}</small></div>}
          {error ? <ErrorState title="Office 状态暂不可用" description={error}/> : loading && !snapshot ? <div className="r3-office-loading"><i/>正在读取真实 Agent 状态…</div> : filteredAgents.length ? (
            <div className="r3-office-agent-list">{filteredAgents.map(agent=><div key={agent.agent_id} className="r3-office-agent"><span className={agent.status==='running'?'r3-office-agent-avatar status-running':'r3-office-agent-avatar'}>{agent.name.slice(0,1)}</span><span><b>{agent.name}</b><small>{agent.role.slice(0,38)}</small></span><em className={agent.status==='running'?'running':'idle'}>{agent.status==='running'?'运行中':'空闲'}</em></div>)}</div>
          ) : <EmptyState title="暂无 Agent 数据" description="当前租户尚无已启用 Agent。状态与运行任务均来自 Office state API，不使用演示数据。"/>}
          <div className="r3-office-status-note"><i/>Agent 定义和运行状态来自 /api/platform/v1/office/state；每 15 秒刷新。服务端暂未提供楼层/工位分配，空间坐标仅为默认布局。</div>
        </aside>
      </div>
    </div>
  )
}
