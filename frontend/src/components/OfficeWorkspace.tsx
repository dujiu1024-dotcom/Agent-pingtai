import { useMemo, useRef, useState } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { Html, OrbitControls } from '@react-three/drei'
import type { Group } from 'three'
import { useQuery } from '@tanstack/react-query'

import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import { apiClient } from '@/lib/api/api-client'
import './office-workspace.css'

type Floor = { id: string; label: string; name: string; description: string }
type AgentStatus = 'idle' | 'queued' | 'running' | 'collaborating' | 'waiting_approval' | 'paused' | 'failed' | 'offline'
type OfficeAgentPayload = {
  agent_id?: string
  id?: string
  name?: string
  title?: string
  desc?: string
  description?: string
  command?: string
  role?: string
  status?: string
  state?: string
  current_status?: string
  floor_id?: string
  zone_id?: string
  desk_id?: string
  current_task?: string | { task_id?: string; title?: string }
  position?: { x?: number; y?: number; z?: number }
  x?: number
  z?: number
  [key: string]: unknown
}
type OfficeRun = {
  id?: string
  run_id?: string
  agent_id?: string
  role?: string
  task_id?: string
  model?: string
  status?: string
  ok?: boolean
  error?: string | null
  created_at?: string | null
  updated_at?: string | null
  started_at?: string | null
  [key: string]: unknown
}
type OfficeSnapshot = {
  agents?: OfficeAgentPayload[]
  runs?: OfficeRun[]
  floors?: Array<Partial<Floor> & { id: string }>
  zones?: Array<{ id: string; name?: string; label?: string; x?: number; z?: number; width?: number; depth?: number }>
  desks?: Array<{ id: string; floor_id?: string; x?: number; z?: number; zone_id?: string }>
  events?: { pending?: number }
}
type OfficeAgent = {
  agent_id: string
  name: string
  role: string
  status: AgentStatus
  floor_id?: string
  zone_id?: string
  desk_id?: string
  x?: number
  z?: number
  task?: string
}
type DeskLayout = { id: string; x: number; z: number; floor_id?: string; zone_id?: string }

const defaultFloors: Floor[] = [
  { id: 'floor-03', label: '03', name: '创新与协作层', description: '会议、协作与项目空间' },
  { id: 'floor-02', label: '02', name: '智能体工作层', description: 'Agent 工位与部门区域' },
  { id: 'floor-01', label: '01', name: '运营与知识层', description: '知识中心与运维空间' },
]
const defaultZones = [
  { id: 'focus', name: '专注工位', subtitle: 'FOCUS PODS', x: -6.1, z: -3.8, width: 5.4, depth: 4.5 },
  { id: 'studio', name: '团队工作室', subtitle: 'TEAM STUDIO', x: 1.1, z: -3.8, width: 7.2, depth: 4.5 },
  { id: 'meeting', name: '协作会议区', subtitle: 'MEETING HUB', x: -6.1, z: 3.2, width: 5.4, depth: 4.7 },
  { id: 'knowledge', name: '知识中心', subtitle: 'KNOWLEDGE', x: 1.1, z: 3.2, width: 3.5, depth: 4.7 },
  { id: 'ops', name: '运营中枢', subtitle: 'OPS CENTER', x: 5.8, z: 3.2, width: 2.5, depth: 4.7 },
]
const defaultDesks: DeskLayout[] = Array.from({ length: 21 }, (_, index) => ({
  id: `desk-${String(index + 1).padStart(2, '0')}`,
  x: -7.2 + (index % 7) * 2.35,
  z: -4.6 + Math.floor(index / 7) * 4.5,
}))
const statusLabels: Record<AgentStatus, string> = {
  idle: '空闲', queued: '排队中', running: '运行中', collaborating: '协作中',
  waiting_approval: '等待审批', paused: '已暂停', failed: '失败', offline: '离线',
}
const statusColors: Record<AgentStatus, string> = {
  idle: '#8d9bb3', queued: '#d9a34e', running: '#2f6af0', collaborating: '#27a879',
  waiting_approval: '#e2a33c', paused: '#9a88d5', failed: '#d86d78', offline: '#9aa5b8',
}
const validStatus = (value: unknown): AgentStatus | undefined =>
  typeof value === 'string' && value in statusLabels ? value as AgentStatus : undefined

function latestRunFor(agent: OfficeAgentPayload, runs: OfficeRun[]): OfficeRun | undefined {
  const id = agent.agent_id ?? agent.id
  const name = agent.name ?? agent.title
  return runs
    .filter((run) => (id && run.agent_id === id) || (name && run.role === name))
    .sort((a, b) => Date.parse(b.updated_at ?? b.created_at ?? b.started_at ?? '') - Date.parse(a.updated_at ?? a.created_at ?? a.started_at ?? ''))[0]
}

export function normalizeOfficeAgents(snapshot: OfficeSnapshot): OfficeAgent[] {
  return (snapshot.agents ?? []).map((agent, index) => {
    const run = latestRunFor(agent, snapshot.runs ?? [])
    const status = validStatus(agent.status ?? agent.current_status ?? agent.state)
      ?? validStatus(run?.status)
      ?? (run?.error ? 'failed' : run?.ok === true ? 'idle' : 'offline')
    const position = agent.position
    const deskIndex = (agent.desk_id?.match(/(\d+)$/)?.[1] ?? '')
    const fallbackDesk = deskIndex ? defaultDesks[Number(deskIndex) - 1] : undefined
    const currentTask = typeof agent.current_task === 'string'
      ? agent.current_task
      : agent.current_task?.title ?? agent.current_task?.task_id ?? run?.task_id
    return {
      agent_id: agent.agent_id ?? agent.id ?? `unknown-agent-${index}`,
      name: agent.title ?? agent.name ?? agent.agent_id ?? agent.id ?? '未命名 Agent',
      role: agent.role ?? agent.command ?? agent.description ?? agent.desc ?? 'Agent',
      status,
      floor_id: agent.floor_id,
      zone_id: agent.zone_id,
      desk_id: agent.desk_id,
      x: typeof position?.x === 'number' ? position.x : typeof agent.x === 'number' ? agent.x : fallbackDesk?.x,
      z: typeof position?.z === 'number' ? position.z : typeof agent.z === 'number' ? agent.z : fallbackDesk?.z,
      task: currentTask,
    }
  })
}

function AgentMarker({ agent, selected, onSelect }: { agent: OfficeAgent; selected: boolean; onSelect: (id: string) => void }) {
  const group = useRef<Group>(null)
  useFrame(({ clock }) => {
    if (!group.current) return
    const t = clock.getElapsedTime()
    group.current.position.y = 0.03 + Math.sin(t * 2.1 + agent.agent_id.length) * 0.045
    group.current.rotation.y = Math.sin(t * 0.8 + agent.agent_id.length) * 0.08
  })
  const color = statusColors[agent.status]
  return (
    <group ref={group} position={[agent.x ?? 0, 0, agent.z ?? 0]} onClick={(event) => { event.stopPropagation(); onSelect(agent.agent_id) }}>
      <mesh position={[0, 0.98, 0]} castShadow>
        <capsuleGeometry args={[0.27, 0.48, 5, 10]} />
        <meshStandardMaterial color={color} roughness={0.48} />
      </mesh>
      <mesh position={[0, 1.42, 0]} castShadow>
        <sphereGeometry args={[0.23, 16, 16]} />
        <meshStandardMaterial color="#f4f7fc" roughness={0.35} />
      </mesh>
      <mesh position={[0, 1.43, 0.205]}>
        <sphereGeometry args={[0.035, 8, 8]} />
        <meshBasicMaterial color="#1c2540" />
      </mesh>
      <mesh position={[0, 0.035, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[selected ? 0.62 : 0.48, selected ? 0.72 : 0.56, 28]} />
        <meshBasicMaterial color={selected ? '#2f6af0' : color} />
      </mesh>
      <Html position={[0, 1.82, 0]} center distanceFactor={12} occlude={false}>
        <button className={selected ? 'office-agent-tag selected' : 'office-agent-tag'} onClick={(event) => { event.stopPropagation(); onSelect(agent.agent_id) }}>
          <i style={{ background: color }} />{agent.name}<small>{statusLabels[agent.status]}</small>
        </button>
      </Html>
    </group>
  )
}

function Desk({ desk, selected, occupied, onSelect }: { desk: DeskLayout; selected: boolean; occupied: boolean; onSelect: (id: string) => void }) {
  const [hovered, setHovered] = useState(false)
  const surface = selected ? '#dce7ff' : hovered ? '#edf2ff' : '#f7f9fd'
  return (
    <group position={[desk.x, 0, desk.z]} onClick={(event) => { event.stopPropagation(); onSelect(desk.id) }}
      onPointerOver={(event) => { event.stopPropagation(); setHovered(true) }} onPointerOut={() => setHovered(false)}>
      <mesh position={[0, 0.58, 0]} castShadow receiveShadow><boxGeometry args={[1.35, 0.12, 0.82]} /><meshStandardMaterial color={surface} roughness={0.72} /></mesh>
      {([[-0.55, -0.27], [0.55, -0.27], [-0.55, 0.27], [0.55, 0.27]] as const).map(([dx, dz]) => (
        <mesh key={`${dx}-${dz}`} position={[dx, 0.29, dz]}><boxGeometry args={[0.07, 0.55, 0.07]} /><meshStandardMaterial color="#9aa9c2" /></mesh>
      ))}
      <mesh position={[0, 0.91, -0.17]} castShadow><boxGeometry args={[0.62, 0.42, 0.07]} /><meshStandardMaterial color="#243654" roughness={0.35} /></mesh>
      <mesh position={[0, 0.91, -0.125]}><boxGeometry args={[0.53, 0.32, 0.018]} /><meshStandardMaterial color="#b9d0ff" emissive="#4779dc" emissiveIntensity={0.14} /></mesh>
      <mesh position={[0, 0.07, 0.78]} castShadow><boxGeometry args={[0.66, 0.12, 0.62]} /><meshStandardMaterial color={occupied ? '#2f6af0' : '#c8d2e2'} /></mesh>
      <mesh position={[0, 0.39, 1.02]} castShadow><boxGeometry args={[0.66, 0.55, 0.1]} /><meshStandardMaterial color={occupied ? '#8eabed' : '#d8e0ec'} /></mesh>
      {selected && <mesh position={[0, 0.025, 0]} rotation={[-Math.PI / 2, 0, 0]}><ringGeometry args={[0.82, 0.94, 32]} /><meshBasicMaterial color="#2f6af0" /></mesh>}
    </group>
  )
}

function OfficeScene({ floorId, agents, desks, selectedDesk, selectedAgent, onSelectDesk, onSelectAgent }: {
  floorId: string
  agents: OfficeAgent[]
  desks: DeskLayout[]
  selectedDesk: string | null
  selectedAgent: string | null
  onSelectDesk: (id: string) => void
  onSelectAgent: (id: string) => void
}) {
  const visibleAgents = agents.filter((agent) => agent.floor_id === floorId && agent.x !== undefined && agent.z !== undefined)
  const occupiedDesks = new Set(visibleAgents.map((agent) => agent.desk_id).filter(Boolean))
  return (
    <>
      <color attach="background" args={['#aebbd8']} />
      <fog attach="fog" args={['#aebbd8', 19, 34]} />
      <ambientLight intensity={1.15} />
      <directionalLight position={[8, 14, 9]} intensity={2.1} castShadow shadow-mapSize-width={2048} shadow-mapSize-height={2048} />
      <hemisphereLight args={['#ffffff', '#7184a5', 0.55]} />
      <group position={[0, -0.18, 0]}>
        <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow><planeGeometry args={[21, 14]} /><meshStandardMaterial color="#e7edf7" roughness={0.92} /></mesh>
        <gridHelper args={[21, 21, '#c1cde0', '#d4ddeb']} position={[0, 0.012, 0]} />
        {defaultZones.map((zone) => (
          <group key={zone.id} position={[zone.x, 0.025, zone.z]}>
            <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow><planeGeometry args={[zone.width, zone.depth]} /><meshStandardMaterial color="#f4f7fc" roughness={0.95} /></mesh>
            <mesh position={[0, 0.035, -zone.depth / 2 + 0.12]}><boxGeometry args={[zone.width, 0.06, 0.12]} /><meshStandardMaterial color="#d2def3" /></mesh>
            <Html position={[0, 0.22, -zone.depth / 2 + 0.32]} center distanceFactor={17} occlude={false}><span className="office-zone-3d-label">{zone.name}</span></Html>
          </group>
        ))}
        {desks.map((desk) => <Desk key={desk.id} desk={desk} selected={selectedDesk === desk.id} occupied={occupiedDesks.has(desk.id)} onSelect={onSelectDesk} />)}
        {visibleAgents.map((agent) => <AgentMarker key={agent.agent_id} agent={agent} selected={selectedAgent === agent.agent_id} onSelect={onSelectAgent} />)}
        <group position={[-9.8, 0.45, 0]}><mesh castShadow><boxGeometry args={[0.24, 0.9, 13.8]} /><meshStandardMaterial color="#b7c5dc" /></mesh></group>
        <group position={[0, 0.4, -6.7]}><mesh castShadow><boxGeometry args={[20.4, 0.8, 0.22]} /><meshStandardMaterial color="#b7c5dc" /></mesh></group>
      </group>
      <OrbitControls makeDefault minDistance={10} maxDistance={27} maxPolarAngle={Math.PI / 2.08} minPolarAngle={0.35} enableDamping dampingFactor={0.08} target={[0, 0.2, 0]} />
    </>
  )
}

export function OfficeWorkspace() {
  const [floorId, setFloorId] = useState(defaultFloors[1].id)
  const [view, setView] = useState<'space' | 'top'>('space')
  const [selectedDesk, setSelectedDesk] = useState<string | null>(null)
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const snapshot = useQuery({
    queryKey: ['office-state'],
    queryFn: () => apiClient.get<{ ok: boolean; data: OfficeSnapshot }>('/platform/v1/office/state'),
    refetchInterval: 10_000,
    retry: false,
  })
  const data = snapshot.data?.data
  const floors = data?.floors?.length ? data.floors.map((floor, index) => ({
    id: floor.id, label: floor.label ?? String(data.floors!.length - index).padStart(2, '0'),
    name: floor.name ?? floor.id, description: floor.description ?? '',
  })) : defaultFloors
  const agents = useMemo(() => normalizeOfficeAgents(data ?? {}), [data])
  const desks = useMemo(() => data?.desks?.length
    ? data.desks.filter((desk) => !desk.floor_id || desk.floor_id === floorId).map((desk, index) => ({
      id: desk.id, x: desk.x ?? defaultDesks[index % defaultDesks.length].x,
      z: desk.z ?? defaultDesks[index % defaultDesks.length].z, floor_id: desk.floor_id, zone_id: desk.zone_id,
    }))
    : defaultDesks, [data?.desks, floorId])
  const floor = floors.find((item) => item.id === floorId) ?? floors[0]
  const visibleAgents = agents.filter((agent) => (!agent.floor_id || agent.floor_id === floorId)
    && `${agent.name} ${agent.agent_id} ${agent.desk_id ?? ''}`.toLowerCase().includes(search.toLowerCase()))
  const sceneAgents = visibleAgents.filter((agent) => agent.floor_id === floorId && agent.x !== undefined && agent.z !== undefined)
  const pendingEvents = data?.events?.pending
  const selected = agents.find((agent) => agent.agent_id === selectedAgent)
  const selectedDeskInfo = desks.find((desk) => desk.id === selectedDesk)
  const apiState = snapshot.isPending ? '正在连接 Office 服务' : snapshot.isError ? 'Office 服务连接失败' : '实时状态已同步'

  return (
    <main className="office-workspace">
      <header className="office-topbar">
        <div className="office-brand"><span className="office-brand-mark">A</span><span><strong>AGENT OFFICE</strong><small>智能体数字办公空间</small></span></div>
        <div className="office-floor-tabs" role="group" aria-label="楼层导航">
          {floors.map((item) => <button key={item.id} className={item.id === floorId ? 'office-floor-tab active' : 'office-floor-tab'}
            onClick={() => { setFloorId(item.id); setSelectedDesk(null); setSelectedAgent(null) }} aria-pressed={item.id === floorId}>
            <strong>{item.label}</strong><span>{item.name}</span>
          </button>)}
        </div>
        <div className="office-top-actions">
          <span className={snapshot.isError ? 'office-sync-state error' : 'office-sync-state'}><i />{apiState}</span>
          <button className={view === 'space' ? 'office-view-button active' : 'office-view-button'} onClick={() => setView('space')} aria-pressed={view === 'space'}>空间视角</button>
          <button className={view === 'top' ? 'office-view-button active' : 'office-view-button'} onClick={() => setView('top')} aria-pressed={view === 'top'}>俯视图</button>
        </div>
      </header>
      <section className="office-heading">
        <div><p className="office-eyebrow">DIGITAL WORKSPACE / {floor.label}F</p><h1>{floor.name}</h1><p>{floor.description} <span>·</span> 交互式 3D 空间</p></div>
        <div className="office-heading-stats"><div><strong>{desks.length}</strong><span>工位总数</span></div><i /><div><strong>{visibleAgents.length}</strong><span>Agent 快照</span></div><i /><div><strong>{pendingEvents ?? '—'}</strong><span>待处理事件</span></div></div>
      </section>
      <section className="office-main-grid">
        <div className="office-viewport">
          <div className="office-viewport-caption"><span><i /> 3D SPACE</span><span>拖拽旋转 · 滚轮缩放 · 点击工位或 Agent 查看</span></div>
          <div className={view === 'top' ? 'office-canvas office-canvas-top' : 'office-canvas'}>
            <Canvas key={view} shadows dpr={[1, 1.6]} camera={{ position: view === 'top' ? [0, 22, 0.01] : [13, 12, 16], fov: view === 'top' ? 42 : 38 }}>
              <OfficeScene floorId={floorId} agents={sceneAgents} desks={desks} selectedDesk={selectedDesk} selectedAgent={selectedAgent} onSelectDesk={setSelectedDesk} onSelectAgent={setSelectedAgent} />
            </Canvas>
          </div>
          <div className="office-map-legend"><span><i className="legend-desk" />标准工位</span><span><i className="legend-selected" />已选工位</span><span><i className="legend-agent" />Agent 状态穿透</span><small>Agent 仅在服务端返回位置字段时落位；状态来自实时快照 / Run 数据</small></div>
        </div>
        <aside className="office-inspector">
          <div className="office-inspector-title"><div><p className="office-eyebrow">SPACE EXPLORER</p><h2>空间信息</h2></div><span className="office-floor-chip">{floor.label}F</span></div>
          <label className="office-search"><span>⌕</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索 Agent / 工位" aria-label="搜索 Agent 或工位" /></label>
          <div className="office-inspector-section"><div className="office-section-heading">楼层概览 <span>OVERVIEW</span></div>
            <div className="office-floor-overview"><div><strong>{desks.length}</strong><span>工位</span></div><div><strong>{defaultZones.length.toString().padStart(2, '0')}</strong><span>功能区域</span></div><div><strong>{visibleAgents.length.toString().padStart(2, '0')}</strong><span>Agent</span></div></div>
          </div>
          <div className="office-inspector-section"><div className="office-section-heading">功能区域 <span>{defaultZones.length} ZONES</span></div>
            <div className="office-zone-list">{defaultZones.map((zone) => <div className="office-zone-row" key={zone.id}><span className="office-zone-dot" /><span><strong>{zone.name}</strong><small>{zone.subtitle}</small></span><span className="office-zone-count">—</span></div>)}</div>
          </div>
          <div className="office-inspector-section office-agent-section"><div className="office-section-heading">Agent 状态 <span>{sceneAgents.length} IN SCENE</span></div>
            {snapshot.isPending ? <LoadingState label="正在读取 Agent 实时状态" /> : snapshot.isError ? <ErrorState title="Office 状态读取失败" description={snapshot.error instanceof Error ? snapshot.error.message : 'Office 快照请求失败'} onRetry={() => void snapshot.refetch()} /> : visibleAgents.length ? visibleAgents.map((agent) => <button type="button" className={selectedAgent === agent.agent_id ? 'office-agent-row selected' : 'office-agent-row'} key={agent.agent_id} onClick={() => setSelectedAgent(agent.agent_id)}><span className={`office-agent-status status-${agent.status}`} /><span><strong>{agent.name}</strong><small>{agent.role} · {statusLabels[agent.status]}{agent.task ? ` · ${agent.task}` : ''}</small></span></button>) : <EmptyState title="暂无实时 Agent 数据" description="服务端快照返回 Agent 后会在此显示；不会用演示数据填充。" />}
          </div>
          <div className="office-selected-desk">{selected ? <><span>当前 Agent</span><strong>{selected.name}</strong><small>{statusLabels[selected.status]} · {selected.task ?? selected.agent_id}</small></> : selectedDeskInfo ? <><span>当前选中工位</span><strong>{selectedDeskInfo.id.toUpperCase()}</strong><small>{selectedDeskInfo.zone_id ?? '尚未关联 Agent'}</small></> : <><span>空间详情</span><strong>选择一个工位或 Agent</strong><small>在 3D 场景中交互查看详情</small></>}</div>
          <p className="office-api-note">数据源：/platform/v1/office/state · 10 秒轮询。缺少位置元数据的 Agent 仍会列在侧栏，但不会被错误放置到虚构工位。</p>
        </aside>
      </section>
    </main>
  )
}
