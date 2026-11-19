import { useMemo, useState } from 'react'
import { EmptyState } from '@/components/States'
import { OfficeScene3D } from '@/components/OfficeScene3D'
import './office-workspace.css'

type Floor = { id: string; label: string; name: string; description: string }
type Zone = { id: string; label: string; kind: string; description: string }
type AgentStatus = 'idle' | 'queued' | 'running' | 'collaborating' | 'waiting_approval' | 'paused' | 'failed' | 'offline'
type OfficeAgent = { agent_id: string; name: string; role: string; status: AgentStatus; floor_id: string; zone_id: string; desk_id?: string }

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
// The current Office API contract does not yet expose a confirmed snapshot stream.
// Keep the scene fully rendered while refusing to fabricate Agent identities or live status.
const agents: OfficeAgent[] = []

const statusLabels: Record<AgentStatus, string> = {
  idle: '空闲', queued: '排队中', running: '运行中', collaborating: '协作中',
  waiting_approval: '等待审批', paused: '已暂停', failed: '失败', offline: '离线',
}

export function OfficeWorkspace() {
  const [floorId, setFloorId] = useState(floors[1].id)
  const [zoneId, setZoneId] = useState<string | null>(null)
  const [view, setView] = useState<'immersive' | '2d'>('immersive')
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const floor = useMemo(() => floors.find((item) => item.id === floorId) ?? floors[1], [floorId])
  const visibleAgents = agents.filter((agent) => agent.floor_id === floorId && (!zoneId || agent.zone_id === zoneId))
  const selectedZone = zones.find((zone) => zone.id === zoneId)
  const filteredAgents = visibleAgents.filter((agent) => `${agent.name} ${agent.agent_id} ${agent.desk_id ?? ''}`.toLowerCase().includes(search.toLowerCase()))

  function selectFloor(id: string) {
    setFloorId(id)
    setZoneId(null)
    setSelectedAgent(null)
  }

  return (
    <div className="r3-office">
      <header className="r3-office-toolbar">
        <div className="r3-floor-switch" role="group" aria-label="楼层导航">
          {floors.map((item) => (
            <button key={item.id} className={item.id === floorId ? 'selected' : ''} onClick={() => selectFloor(item.id)} aria-pressed={item.id === floorId}>
              <b>{item.label}</b><span>{item.name}</span>
            </button>
          ))}
        </div>
        <div className="r3-office-view-switch" role="group" aria-label="办公室视图">
          <button className={view === 'immersive' ? 'selected' : ''} onClick={() => setView('immersive')} aria-pressed={view === 'immersive'}>◈ 沉浸空间</button>
          <button className={view === '2d' ? 'selected' : ''} onClick={() => setView('2d')} aria-pressed={view === '2d'}>▦ 2D 导览</button>
        </div>
      </header>

      <section className="r3-office-overview">
        <div><span className="r3-office-eyebrow">DIGITAL OFFICE / FLOOR {floor.label}</span><h2>{floor.name}</h2><p>{floor.description}</p></div>
        <div className="r3-office-stats"><span><i className="r3-live-dot"/>场景已加载</span><b>{visibleAgents.length.toString().padStart(2, '0')} <small>在线 Agent</small></b><small>状态流待接入</small></div>
      </section>

      <div className="r3-office-grid">
        <section className={view === 'immersive' ? 'r3-scene-panel' : 'r3-scene-panel is-2d'}>
          <div className="r3-scene-panel-head"><div><b>空间总览</b><span>INTERACTIVE 3D ENVIRONMENT</span></div><div className="r3-scene-key"><i/>空间区域 <i className="agent-key"/>Agent 状态</div></div>
          {view === 'immersive' ? (
            <OfficeScene3D zones={zones} selectedZone={zoneId} onSelectZone={(id) => setZoneId(id === zoneId ? null : id)} agents={visibleAgents} />
          ) : (
            <div className="r3-office-2d-map">
              {zones.map((zone, index) => <button key={zone.id} className={zoneId === zone.id ? 'active' : ''} onClick={() => setZoneId(id => id === zone.id ? null : zone.id)}><span>{['▦','⌂','◫','▤','⌘'][index]}</span><b>{zone.label}</b><small>{zone.description}</small><em>{agents.filter(agent => agent.floor_id === floorId && agent.zone_id === zone.id).length} Agent</em></button>)}
            </div>
          )}
          <footer className="r3-scene-footer"><span>拖动旋转 · 滚轮缩放 · 点击区域查看详情</span><span>布局：默认楼层模板</span></footer>
        </section>

        <aside className="r3-office-inspector">
          <div className="r3-inspector-title"><div><span className="r3-office-eyebrow">SPACE EXPLORER</span><h3>空间与 Agent</h3></div><b>{visibleAgents.length} <small>AGENTS</small></b></div>
          <label className="r3-office-search"><span>⌕</span><input value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索 Agent / 工位" aria-label="搜索 Agent 或工位"/></label>
          <div className="r3-zone-filters">
            <button className={zoneId === null ? 'active' : ''} onClick={() => setZoneId(null)}>全部空间 <b>{agents.filter(agent => agent.floor_id === floorId).length}</b></button>
            {zones.map(zone => <button key={zone.id} className={zoneId === zone.id ? 'active' : ''} onClick={() => setZoneId(id => id === zone.id ? null : zone.id)}>{zone.label}<b>{agents.filter(agent => agent.floor_id === floorId && agent.zone_id === zone.id).length}</b></button>)}
          </div>
          {selectedZone && <div className="r3-zone-detail"><b>{selectedZone.label}</b><p>{selectedZone.description}</p><small>{selectedZone.kind} · {selectedZone.id}</small></div>}
          {filteredAgents.length ? <div className="r3-office-agent-list">{filteredAgents.map(agent => <button key={agent.agent_id} className="r3-office-agent" onClick={() => setSelectedAgent(agent.agent_id)}><span className={`r3-office-agent-avatar status-${agent.status}`}>{agent.name.slice(0,1)}</span><span><b>{agent.name}</b><small>{agent.role} · {statusLabels[agent.status]}</small></span><i>›</i></button>)}</div> : <EmptyState title="暂无实时 Agent" description="空间布局已就绪。接入 Office snapshot 与事件流后，将在对应工位显示 Agent、当前任务及状态动画。" />}
          {selectedAgent && <div className="r3-zone-detail"><b>Agent 详情</b><p>Agent ID：{selectedAgent}</p><small>详情数据需由 Agent API 提供</small></div>}
          <div className="r3-office-status-note"><i/>当前为真实空数据状态。不会使用原型演示角色冒充在线 Agent。</div>
        </aside>
      </div>
    </div>
  )
}
