import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import { apiClient } from '@/lib/api/api-client'
import './office-workspace.css'

type Floor = { id: string; label: string; name: string; description: string }
type Zone = { id: string; label: string; kind: string; description: string }
type AgentStatus =
  | 'idle'
  | 'queued'
  | 'running'
  | 'collaborating'
  | 'waiting_approval'
  | 'paused'
  | 'failed'
  | 'offline'

const floors: Floor[] = [
  { id: 'floor-03', label: '03', name: '创新与协作层', description: '会议、协作与项目空间' },
  { id: 'floor-02', label: '02', name: '智能体工作层', description: 'Agent 工位与部门区域' },
  { id: 'floor-01', label: '01', name: '运营与知识层', description: '知识中心与运维空间' },
]

const zones: Zone[] = [
  { id: 'open-work', label: '开放工位区', kind: 'workstations', description: 'Agent 工位与工作状态' },
  { id: 'team-studios', label: '部门工作室', kind: 'departments', description: '团队空间分组（可选）' },
  { id: 'meeting-hub', label: '协作会议室', kind: 'meetings', description: '协作 Run 与参与者' },
  { id: 'knowledge-center', label: '知识中心', kind: 'knowledge', description: '知识资产与引用入口' },
  { id: 'operations-center', label: '运维中心', kind: 'operations', description: '运行告警与失败任务' },
]

type OfficeAgent = {
  agent_id: string
  name: string
  role: string
  status: AgentStatus
  floor_id: string
  zone_id: string
  desk_id?: string
}

type OfficeRun = {
  id: string
  role: string
  task_id: string
  model: string
  ok: boolean
  error?: string | null
  created_at?: string | null
}

type OfficeSnapshot = {
  agents: Array<{ name: string; title: string; desc: string; command: string }>
  runs: OfficeRun[]
  events?: { pending?: number }
  tasks?: unknown[]
}

const statusLabels: Record<AgentStatus, string> = {
  idle: '空闲',
  queued: '排队中',
  running: '运行中',
  collaborating: '协作中',
  waiting_approval: '等待审批',
  paused: '已暂停',
  failed: '失败',
  offline: '离线',
}

export function OfficeWorkspace() {
  const [floorId, setFloorId] = useState(floors[1].id)
  const [zoneId, setZoneId] = useState<string | null>(null)
  const [view, setView] = useState<'isometric' | 'top' | '2d'>('isometric')
  const [selected, setSelected] = useState<{ type: 'zone' | 'agent'; id: string } | null>(null)
  const [search, setSearch] = useState('')
  const floor = useMemo(() => floors.find((item) => item.id === floorId) ?? floors[1], [floorId])

  const snapshot = useQuery({
    queryKey: ['office-state'],
    queryFn: () =>
      apiClient.get<{ ok: boolean; data: OfficeSnapshot }>(
        '/platform/v1/office/state',
      ),
    refetchInterval: 30_000,
    retry: false,
  })

  const agents = useMemo<OfficeAgent[]>(() => {
    const runs = snapshot.data?.data.runs ?? []
    return (snapshot.data?.data.agents ?? []).map((agent, index) => {
      const latest = runs.find((run) => run.role === agent.name)
      const status: AgentStatus = latest ? (latest.error ? 'failed' : 'idle') : 'idle'
      return {
        agent_id: agent.name,
        name: agent.title || agent.name,
        role: agent.command,
        status,
        // 楼层/分区为展示性布局，不参与业务口径
        floor_id: floors[index % floors.length].id,
        zone_id: zones[index % zones.length].id,
      }
    })
  }, [snapshot.data])

  const visibleAgents = agents.filter((agent) => agent.floor_id === floorId && (!zoneId || agent.zone_id === zoneId))
  const selectedZone = zones.find((zone) => zone.id === (selected?.type === 'zone' ? selected.id : zoneId))
  const pendingEvents = snapshot.data?.data.events?.pending ?? 0

  function selectFloor(id: string) {
    setFloorId(id)
    setZoneId(null)
    setSelected(null)
  }

  return (
    <div className="office-workspace">
      <section className="office-toolbar" aria-label="办公室视图控制">
        <div className="office-floor-tabs" role="group" aria-label="楼层导航">
          {floors.map((item) => (
            <button key={item.id} className={item.id === floorId ? 'office-floor-tab active' : 'office-floor-tab'} onClick={() => selectFloor(item.id)} aria-pressed={item.id === floorId}>
              <strong>{item.label}</strong><span>{item.name}</span>
            </button>
          ))}
        </div>
        <div className="office-view-switch" role="group" aria-label="场景视图">
          <button className={view === 'isometric' ? 'active' : ''} onClick={() => setView('isometric')} aria-pressed={view === 'isometric'}>空间视图</button>
          <button className={view === 'top' ? 'active' : ''} onClick={() => setView('top')} aria-pressed={view === 'top'}>俯视</button>
          <button className={view === '2d' ? 'active' : ''} onClick={() => setView('2d')} aria-pressed={view === '2d'}>2D 降级</button>
        </div>
      </section>

      <section className="office-summary">
        <div><p className="eyebrow">数字办公室 / {floor.label} 层</p><h2>{floor.name}</h2><p>{floor.description}</p></div>
        <div className="office-live-state">
          <span className="office-live-dot" />
          {snapshot.isPending ? '正在读取 Office 快照' : `实时快照 · ${agents.length} 个 Agent · ${pendingEvents} 个待处理事件`}
          <small>数据来源 /platform/v1/office/state，每 30 秒刷新</small>
        </div>
      </section>

      {snapshot.isError && (
        <ErrorState
          description={
            snapshot.error instanceof Error ? snapshot.error.message : 'Office 快照加载失败'
          }
          onRetry={() => void snapshot.refetch()}
        />
      )}

      <div className="office-layout">
        <section className={`office-scene office-scene-${view}`} aria-label={view === '2d' ? '办公室二维空间示意图' : '办公室空间示意图'}>
          <div className="office-scene-header"><span>空间布局预览</span><span className="office-scene-tag">{view === '2d' ? '2D 无障碍视图' : view === 'top' ? '俯视视图' : '轴测视图'}</span></div>
          <div className="office-stage">
            <div className="office-building">
              <div className="office-building-top"><span>智能体办公室</span><span>{floor.label} 层</span></div>
              <div className="office-zone-grid">
                {zones.map((zone, index) => (
                  <button key={zone.id} className={`office-zone office-zone-${index + 1}${zoneId === zone.id ? ' selected' : ''}`} onClick={() => { setZoneId(zone.id); setSelected({ type: 'zone', id: zone.id }) }} aria-pressed={zoneId === zone.id}>
                    <span className="office-zone-icon" aria-hidden="true">{['▦', '⌂', '◫', '▤', '⌘'][index]}</span>
                    <strong>{zone.label}</strong>
                    <small>{zone.description}</small>
                    <em>{agents.filter((agent) => agent.floor_id === floorId && agent.zone_id === zone.id).length} 个 Agent</em>
                  </button>
                ))}
              </div>
              <div className="office-building-footer"><span>布局 · {floor.id}</span><span>布局为展示性分区</span></div>
            </div>
          </div>
          <div className="office-scene-legend"><span><i className="legend-idle" />空闲</span><span><i className="legend-running" />运行中</span><span><i className="legend-waiting" />等待审批</span><span><i className="legend-offline" />离线</span><small>状态由最近一次运行推导</small></div>
        </section>

        <aside className="office-inspector">
          <div className="office-inspector-head"><div><p className="eyebrow">空间浏览</p><h3>空间与 Agent</h3></div><span>{visibleAgents.length} 个 Agent</span></div>
          <label className="office-search"><span>⌕</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索 Agent / 工位" aria-label="搜索 Agent 或工位" /></label>
          <div className="office-zone-list">
            <button className={zoneId === null ? 'office-zone-filter active' : 'office-zone-filter'} onClick={() => { setZoneId(null); setSelected(null) }}>全部空间 <span>{agents.filter((agent) => agent.floor_id === floorId).length}</span></button>
            {zones.map((zone) => <button key={zone.id} className={zoneId === zone.id ? 'office-zone-filter active' : 'office-zone-filter'} onClick={() => { setZoneId(zone.id); setSelected({ type: 'zone', id: zone.id }) }}>{zone.label}<span>{agents.filter((agent) => agent.floor_id === floorId && agent.zone_id === zone.id).length}</span></button>)}
          </div>
          {selectedZone && <div className="office-selection"><strong>{selectedZone.label}</strong><p>{selectedZone.description}</p><small>空间 ID：{selectedZone.id}</small></div>}
          {snapshot.isPending ? (
            <LoadingState label="正在读取 Agent 快照" />
          ) : visibleAgents.filter((agent) => `${agent.name} ${agent.agent_id} ${agent.desk_id ?? ''}`.toLowerCase().includes(search.toLowerCase())).length ? (
            <div className="office-agent-list">{visibleAgents.filter((agent) => `${agent.name} ${agent.agent_id} ${agent.desk_id ?? ''}`.toLowerCase().includes(search.toLowerCase())).map((agent) => <button key={agent.agent_id} className="office-agent-row" onClick={() => setSelected({ type: 'agent', id: agent.agent_id })}><span className="office-avatar">{agent.name.slice(0, 1)}</span><span><strong>{agent.name}</strong><small>{agent.role} · {statusLabels[agent.status] ?? '未知状态'}</small></span><span>›</span></button>)}</div>
          ) : <EmptyState title="暂无 Agent 数据" description="当前租户的 Registry 中还没有已发布的 Agent 定义。" />}
          {selected?.type === 'agent' && <div className="office-selection"><strong>Agent 详情</strong><p>Agent ID：{selected.id}</p><small>{agents.find((agent) => agent.agent_id === selected.id)?.role ?? ''}</small></div>}
        </aside>
      </div>
    </div>
  )
}
