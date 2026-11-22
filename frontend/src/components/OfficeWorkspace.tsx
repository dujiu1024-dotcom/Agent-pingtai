import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/lib/api'
import { Canvas } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import './office-workspace.css'

type Floor = { id: string; label: string; name: string; description: string }
type AgentStatus = 'idle' | 'queued' | 'running' | 'collaborating' | 'waiting_approval' | 'paused' | 'failed' | 'offline' | 'unknown'
type OfficeAgent = {
  agent_id: string
  name: string
  role: string
  status: AgentStatus
  floor_id?: string
  desk_id?: string
  task_id?: string
  task_title?: string
  run_id?: string
  model_template?: string
  last_active_at?: string
}
type RegistryAgent = { item_id: string; floor_id?: string; desk_id?: string; definition?: { name?: string; description?: string; floor_id?: string; desk_id?: string } }
type OfficeTask = { task_id: string; title?: string; prompt?: string; agent_id?: string; run_id?: string; model_template?: string; status?: string; updated_at?: string; created_at?: string }
const TASKS_API = '/platform/v1/workbench/tasks'
const AGENTS_API = '/platform/v1/registry/agent'
const listFrom = <T,>(value: unknown): T[] => {
  if (Array.isArray(value)) return value as T[]
  if (!value || typeof value !== 'object') return []
  const record = value as Record<string, unknown>
  for (const key of ['items', 'results', 'data']) {
    const candidate = record[key]
    if (Array.isArray(candidate)) return candidate as T[]
    if (candidate && typeof candidate === 'object' && Array.isArray((candidate as Record<string, unknown>).items)) return (candidate as { items: T[] }).items
  }
  return []
}
const taskStatus = (status = ''): AgentStatus => {
  const value = status.toLowerCase()
  if (['running', 'executing'].includes(value)) return 'running'
  if (['waiting_approval', 'waiting_human'].includes(value)) return 'waiting_approval'
  if (['queued', 'pending'].includes(value)) return 'queued'
  if (value === 'paused') return 'paused'
  if (['failed', 'error'].includes(value)) return 'failed'
  if (['succeeded', 'success', 'completed', 'done'].includes(value)) return 'idle'
  return 'unknown'
}
const taskTimestamp = (task: OfficeTask) => Date.parse(task.updated_at ?? task.created_at ?? '') || 0

const floors: Floor[] = [
  { id: 'floor-03', label: '03', name: '创新与协作层', description: '会议、协作与项目空间' },
  { id: 'floor-02', label: '02', name: '智能体工作层', description: 'Agent 工位与部门区域' },
  { id: 'floor-01', label: '01', name: '运营与知识层', description: '知识中心与运维空间' },
]

const statusLabels: Record<AgentStatus, string> = {
  idle: '空闲',
  queued: '排队中',
  running: '运行中',
  collaborating: '协作中',
  waiting_approval: '等待审批',
  paused: '已暂停',
  failed: '失败',
  offline: '离线',
  unknown: '状态未知',
}

const zones = [
  { id: 'focus', name: '专注工位', subtitle: 'FOCUS PODS', x: -6.1, z: -3.8, width: 5.4, depth: 4.5 },
  { id: 'studio', name: '团队工作室', subtitle: 'TEAM STUDIO', x: 1.1, z: -3.8, width: 7.2, depth: 4.5 },
  { id: 'meeting', name: '协作会议区', subtitle: 'MEETING HUB', x: -6.1, z: 3.2, width: 5.4, depth: 4.7 },
  { id: 'knowledge', name: '知识中心', subtitle: 'KNOWLEDGE', x: 1.1, z: 3.2, width: 3.5, depth: 4.7 },
  { id: 'archive', name: '档案与知识中心', subtitle: 'ARCHIVE / KNOWLEDGE', x: 1.1, z: 3.2, width: 3.5, depth: 4.7 },
  { id: 'ops', name: '运营中枢', subtitle: 'OPS CENTER', x: 5.8, z: 3.2, width: 2.5, depth: 4.7 },
  { id: 'private', name: '独立办公室', subtitle: 'PRIVATE OFFICES', x: 5.8, z: -3.8, width: 2.5, depth: 4.5 },
]

const desks = Array.from({ length: 21 }, (_, index) => ({
  id: `desk-${String(index + 1).padStart(2, '0')}`,
  x: -7.2 + (index % 7) * 2.35,
  z: -4.6 + Math.floor(index / 7) * 4.5,
}))

function Desk({ id, x, z, selected, onSelect }: {
  id: string
  x: number
  z: number
  selected: boolean
  onSelect: (id: string) => void
}) {
  const [hovered, setHovered] = useState(false)
  const surface = selected ? '#dce7ff' : hovered ? '#edf2ff' : '#f7f9fd'
  return (
    <group position={[x, 0, z]} onClick={(event) => { event.stopPropagation(); onSelect(id) }}
      onPointerOver={(event) => { event.stopPropagation(); setHovered(true) }}
      onPointerOut={() => setHovered(false)}>
      <mesh position={[0, 0.58, 0]} castShadow receiveShadow>
        <boxGeometry args={[1.35, 0.12, 0.82]} />
        <meshStandardMaterial color={surface} roughness={0.72} />
      </mesh>
      {([[-0.55, -0.27], [0.55, -0.27], [-0.55, 0.27], [0.55, 0.27]] as const).map(([dx, dz]) => (
        <mesh key={`${dx}-${dz}`} position={[dx, 0.29, dz]}>
          <boxGeometry args={[0.07, 0.55, 0.07]} />
          <meshStandardMaterial color="#9aa9c2" />
        </mesh>
      ))}
      <mesh position={[0, 0.91, -0.17]} castShadow>
        <boxGeometry args={[0.62, 0.42, 0.07]} />
        <meshStandardMaterial color="#243654" roughness={0.35} />
      </mesh>
      <mesh position={[0, 0.91, -0.125]}>
        <boxGeometry args={[0.53, 0.32, 0.018]} />
        <meshStandardMaterial color="#b9d0ff" emissive="#4779dc" emissiveIntensity={0.14} />
      </mesh>
      <mesh position={[0, 0.07, 0.78]} castShadow>
        <boxGeometry args={[0.66, 0.12, 0.62]} />
        <meshStandardMaterial color={selected ? '#2f6af0' : '#c8d2e2'} />
      </mesh>
      <mesh position={[0, 0.39, 1.02]} castShadow>
        <boxGeometry args={[0.66, 0.55, 0.1]} />
        <meshStandardMaterial color={selected ? '#2f6af0' : '#d8e0ec'} />
      </mesh>
      {selected && <mesh position={[0, 0.025, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[0.82, 0.94, 32]} />
        <meshBasicMaterial color="#2f6af0" />
      </mesh>}
    </group>
  )
}

function OfficePlant({ position }: { position: [number, number, number] }) {
  return (
    <group position={position}>
      <mesh position={[0, 0.2, 0]} castShadow><cylinderGeometry args={[0.22, 0.28, 0.4, 12]} /><meshStandardMaterial color="#a8794f" roughness={0.82} /></mesh>
      {Array.from({ length: 5 }, (_, index) => {
        const angle = (index / 5) * Math.PI * 2
        return <mesh key={index} position={[Math.cos(angle) * 0.2, 0.72 + (index % 2) * 0.12, Math.sin(angle) * 0.2]} rotation={[0.18, angle, -0.22]} castShadow>
          <sphereGeometry args={[0.16, 8, 6]} /><meshStandardMaterial color={index % 2 ? '#5d8b69' : '#426b52'} roughness={0.8} />
        </mesh>
      })}
      <mesh position={[0, 0.72, 0]} castShadow><cylinderGeometry args={[0.045, 0.06, 0.75, 8]} /><meshStandardMaterial color="#6d8c65" /></mesh>
    </group>
  )
}

function MeetingTable({ position }: { position: [number, number, number] }) {
  return <group position={position}>
    <mesh position={[0, 0.48, 0]} castShadow receiveShadow><cylinderGeometry args={[1.45, 1.45, 0.12, 32]} /><meshStandardMaterial color="#a8794f" roughness={0.58} /></mesh>
    <mesh position={[0, 0.23, 0]} castShadow><cylinderGeometry args={[0.16, 0.24, 0.46, 12]} /><meshStandardMaterial color="#76553b" /></mesh>
    {Array.from({ length: 6 }, (_, index) => {
      const angle = (index / 6) * Math.PI * 2
      return <group key={index} position={[Math.cos(angle) * 1.9, 0, Math.sin(angle) * 1.9]} rotation={[0, -angle, 0]}>
        <mesh position={[0, 0.32, 0.35]} castShadow><boxGeometry args={[0.62, 0.12, 0.62]} /><meshStandardMaterial color="#d7dfeb" /></mesh>
        <mesh position={[0, 0.63, 0.63]} castShadow><boxGeometry args={[0.62, 0.58, 0.1]} /><meshStandardMaterial color="#b9c7d9" /></mesh>
      </group>
    })}
  </group>
}

function OfficeDecorations() {
  return <>
    <mesh position={[0, 0.035, -6.35]}><boxGeometry args={[19.5, 0.06, 0.16]} /><meshStandardMaterial color="#a8794f" /></mesh>
    <mesh position={[-9.65, 1.45, 0]}><boxGeometry args={[0.12, 2.9, 13.1]} /><meshStandardMaterial color="#c8d3e4" /></mesh>
    <mesh position={[0, 1.5, -6.55]}><boxGeometry args={[19.5, 2.8, 0.12]} /><meshStandardMaterial color="#c8d3e4" /></mesh>
    {[-6.2, -2.1, 2.0, 6.1].map((x) => <group key={x} position={[x, 1.65, -6.46]}>
      <mesh><boxGeometry args={[2.65, 1.45, 0.08]} /><meshStandardMaterial color="#fffaf0" /></mesh>
      <mesh position={[0, 0, 0.055]}><boxGeometry args={[2.35, 1.17, 0.035]} /><meshStandardMaterial color="#b9d8e2" emissive="#8eb8c9" emissiveIntensity={0.08} /></mesh>
      <mesh position={[0, 0, 0.09]}><boxGeometry args={[0.045, 1.18, 0.04]} /><meshStandardMaterial color="#fffaf0" /></mesh>
      <mesh position={[0, 0, 0.09]}><boxGeometry args={[2.36, 0.045, 0.04]} /><meshStandardMaterial color="#fffaf0" /></mesh>
    </group>)}
    <OfficePlant position={[-8.1, 0, -5.1]} /><OfficePlant position={[8.1, 0, -5.1]} />
    <OfficePlant position={[-8.1, 0, 5.1]} /><OfficePlant position={[8.1, 0, 5.1]} />
    <MeetingTable position={[-5.9, 0, 3.1]} />
    <group position={[3.2, 0, 3.4]}>
      <mesh position={[0, 0.3, 0]} castShadow><boxGeometry args={[2.6, 0.58, 0.9]} /><meshStandardMaterial color="#91a9c8" /></mesh>
      <mesh position={[0, 0.64, -0.35]} castShadow><boxGeometry args={[2.6, 0.78, 0.18]} /><meshStandardMaterial color="#718bad" /></mesh>
      <mesh position={[0, 0.62, 0.62]}><boxGeometry args={[1.2, 0.06, 0.7]} /><meshStandardMaterial color="#a8794f" /></mesh>
    </group>
  </>
}

function OfficeScene({ selectedDesk, onSelectDesk, selectedZone, onSelectZone }: { selectedDesk: string | null; onSelectDesk: (id: string) => void; selectedZone: string | null; onSelectZone: (id: string) => void }) {
  return (
    <>
      <color attach="background" args={['#aebbd8']} />
      <fog attach="fog" args={['#aebbd8', 19, 34]} />
      <ambientLight intensity={1.15} />
      <directionalLight position={[8, 14, 9]} intensity={2.1} castShadow shadow-mapSize-width={2048} shadow-mapSize-height={2048} />
      <hemisphereLight args={['#ffffff', '#7184a5', 0.55]} />
      <group position={[0, -0.18, 0]}>
        <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
          <planeGeometry args={[21, 14]} />
          <meshStandardMaterial color="#e7edf7" roughness={0.92} />
        </mesh>
        <gridHelper args={[21, 21, '#c1cde0', '#d4ddeb']} position={[0, 0.012, 0]} />
        {zones.map((zone) => (
          <group key={zone.id} position={[zone.x, 0.025, zone.z]} onClick={(event) => { event.stopPropagation(); onSelectZone(zone.id) }}>
            <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
              <planeGeometry args={[zone.width, zone.depth]} />
              <meshStandardMaterial color={selectedZone === zone.id ? '#e6edff' : '#f4f7fc'} roughness={0.95} />
            </mesh>
            <mesh position={[0, 0.035, -zone.depth / 2 + 0.12]}>
              <boxGeometry args={[zone.width, 0.06, 0.12]} />
              <meshStandardMaterial color="#d2def3" />
            </mesh>
          </group>
        ))}
        <OfficeDecorations />
        {desks.map((desk) => (
          <Desk key={desk.id} {...desk} selected={selectedDesk === desk.id} onSelect={onSelectDesk} />
        ))}
        <group position={[-9.8, 0.45, 0]}>
          <mesh castShadow><boxGeometry args={[0.24, 0.9, 13.8]} /><meshStandardMaterial color="#b7c5dc" /></mesh>
        </group>
        <group position={[0, 0.4, -6.7]}>
          <mesh castShadow><boxGeometry args={[20.4, 0.8, 0.22]} /><meshStandardMaterial color="#b7c5dc" /></mesh>
        </group>
      </group>
      <OrbitControls makeDefault minDistance={10} maxDistance={27} maxPolarAngle={Math.PI / 2.08}
        minPolarAngle={0.35} enableDamping dampingFactor={0.08} target={[0, 0.2, 0]} />
    </>
  )
}

export function OfficeWorkspace() {
  const [floorId, setFloorId] = useState(floors[1].id)
  const [view, setView] = useState<'space' | 'top'>('space')
  const [selectedDesk, setSelectedDesk] = useState<string | null>(null)
  const [selectedZone, setSelectedZone] = useState<string | null>(null)
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [use2D, setUse2D] = useState(false)
  const floor = useMemo(() => floors.find((item) => item.id === floorId) ?? floors[1], [floorId])
  const agentsQuery = useQuery({
    queryKey: ['workspace', 'office', 'agents'],
    queryFn: () => apiClient.get<unknown>(AGENTS_API).then((value) => listFrom<RegistryAgent>(value)),
    staleTime: 30000,
    refetchInterval: 60000,
  })
  const tasksQuery = useQuery({
    queryKey: ['workspace', 'office', 'tasks'],
    queryFn: () => apiClient.get<unknown>(TASKS_API).then((value) => listFrom<OfficeTask>(value)),
    staleTime: 15000,
    refetchInterval: 30000,
  })
  const agents = useMemo(() => (agentsQuery.data ?? []).map((agent): OfficeAgent => {
    const latestTask = (tasksQuery.data ?? []).filter((task) => task.agent_id === agent.item_id)
      .sort((a, b) => taskTimestamp(b) - taskTimestamp(a))[0]
    return {
      agent_id: agent.item_id,
      name: agent.definition?.name || agent.item_id,
      role: agent.definition?.description || 'Agent Registry 条目',
      status: latestTask ? taskStatus(latestTask.status) : 'unknown',
      floor_id: agent.floor_id || agent.definition?.floor_id,
      desk_id: agent.desk_id || agent.definition?.desk_id,
      task_id: latestTask?.task_id,
      task_title: latestTask?.title || latestTask?.prompt,
      run_id: latestTask?.run_id,
      model_template: latestTask?.model_template,
      last_active_at: latestTask?.updated_at || latestTask?.created_at,
    }
  }), [agentsQuery.data, tasksQuery.data])
  const visibleAgents = agents.filter((agent) => (!agent.floor_id || agent.floor_id === floorId) &&
    `${agent.name} ${agent.agent_id} ${agent.desk_id ?? ''}`.toLowerCase().includes(search.toLowerCase()))
  const runningTasks = (tasksQuery.data ?? []).filter((task) => ['running', 'executing'].includes((task.status ?? '').toLowerCase())).length
  const activeAgents = visibleAgents.filter((agent) => ['running', 'collaborating', 'waiting_approval', 'queued'].includes(agent.status)).length
  const dataUnavailable = agentsQuery.isError || tasksQuery.isError
  const selectedDeskAgent = visibleAgents.find((agent) => agent.desk_id === selectedDesk)
  const selectedAgent = agents.find((agent) => agent.agent_id === selectedAgentId)
  const selectedZoneDetails = zones.find((zone) => zone.id === selectedZone)
  useEffect(() => {
    const updateFallback = () => setUse2D(window.innerWidth <= 760 || window.matchMedia('(pointer: coarse)').matches)
    updateFallback()
    window.addEventListener('resize', updateFallback)
    return () => window.removeEventListener('resize', updateFallback)
  }, [])

  return (
    <main className="office-workspace">
      <header className="office-topbar">
        <div className="office-brand"><span className="office-brand-mark">A</span><span><strong>AGENT OFFICE</strong><small>智能体数字办公空间</small></span></div>
        <div className="office-floor-tabs" role="group" aria-label="楼层导航">
          {floors.map((item) => <button key={item.id} className={item.id === floorId ? 'office-floor-tab active' : 'office-floor-tab'}
            onClick={() => { setFloorId(item.id); setSelectedDesk(null); setSelectedAgentId(null); setSelectedZone(null) }} aria-pressed={item.id === floorId}>
            <strong>{item.label}</strong><span>{item.name}</span>
          </button>)}
        </div>
        <div className="office-top-actions">
          <span className={`office-sync-state ${dataUnavailable ? 'is-error' : ''}`}><i />{agentsQuery.isLoading || tasksQuery.isLoading ? '正在同步' : dataUnavailable ? '部分数据不可用' : '数据已同步'}</span>
          <button className={view === 'space' ? 'office-view-button active' : 'office-view-button'} onClick={() => setView('space')}>空间视角</button>
          <button className={view === 'top' ? 'office-view-button active' : 'office-view-button'} onClick={() => setView('top')}>俯视图</button>
          <button className={use2D ? 'office-view-button active' : 'office-view-button'} onClick={() => setUse2D((value) => !value)} aria-pressed={use2D}>2D 列表</button>
        </div>
      </header>

      <section className="office-heading">
        <div><p className="office-eyebrow">DIGITAL WORKSPACE / {floor.label}F</p><h1>{floor.name}</h1><p>{floor.description} <span>·</span> 交互式 3D 空间</p></div>
        <div className="office-heading-stats"><div><strong>{desks.length}</strong><span>工位总数</span></div><i /><div><strong>{activeAgents}</strong><span>活跃 Agent</span></div><i /><div><strong>{runningTasks}</strong><span>执行中任务</span></div></div>
      </section>

      <section className="office-main-grid">
        <div className="office-viewport">
          <div className="office-viewport-caption"><span><i /> {use2D ? '2D ACCESSIBLE MAP' : '3D SPACE'}</span><span>{use2D ? '键盘选择工位与 Agent' : '拖拽旋转 · 滚轮缩放 · 点击工位或区域查看'}</span></div>
          {use2D ? <div className="office-2d-map" aria-label="办公室二维空间地图">
            <div className="office-2d-zones">{zones.map((zone) => <button type="button" key={zone.id} className={selectedZone === zone.id ? 'office-2d-zone selected' : 'office-2d-zone'} onClick={() => setSelectedZone(zone.id)}><strong>{zone.name}</strong><small>{zone.subtitle}</small></button>)}</div>
            <div className="office-2d-desks">{desks.map((desk) => <button type="button" key={desk.id} className={selectedDesk === desk.id ? 'office-2d-desk selected' : 'office-2d-desk'} onClick={() => setSelectedDesk(desk.id)}>{desk.id.toUpperCase()}</button>)}</div>
          </div> : <div className={view === 'top' ? 'office-canvas office-canvas-top' : 'office-canvas'}>
            <Canvas key={view} shadows dpr={[1, 1.5]} camera={{ position: view === 'top' ? [0, 22, 0.01] : [13, 12, 16], fov: view === 'top' ? 42 : 38 }}>
              <OfficeScene selectedDesk={selectedDesk} onSelectDesk={setSelectedDesk} selectedZone={selectedZone} onSelectZone={setSelectedZone} />
            </Canvas>
          </div>}
          <div className="office-map-legend"><span><i className="legend-desk" />标准工位</span><span><i className="legend-selected" />已选工位</span><span><i className="legend-agent" />Agent 状态</span><small>状态由 Agent Registry 与任务执行记录推导；未提供的状态显示为未知</small></div>
        </div>

        <aside className="office-inspector">
          <div className="office-inspector-title"><div><p className="office-eyebrow">SPACE EXPLORER</p><h2>空间信息</h2></div><span className="office-floor-chip">{floor.label}F</span></div>
          <label className="office-search"><span>⌕</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索 Agent / 工位" aria-label="搜索 Agent 或工位" /></label>
          <div className="office-inspector-section"><div className="office-section-heading">楼层概览 <span>OVERVIEW</span></div>
            <div className="office-floor-overview"><div><strong>21</strong><span>工位</span></div><div><strong>05</strong><span>功能区域</span></div><div><strong>{visibleAgents.length.toString().padStart(2, '0')}</strong><span>Registry Agent</span></div></div>
          </div>
          <div className="office-inspector-section"><div className="office-section-heading">功能区域 <span>{zones.length} ZONES</span></div>
            <div className="office-zone-list">{zones.map((zone) => <button type="button" className={selectedZone === zone.id ? 'office-zone-row selected' : 'office-zone-row'} key={zone.id} onClick={() => setSelectedZone(zone.id)}><span className="office-zone-dot" /><span><strong>{zone.name}</strong><small>{zone.subtitle}</small></span><span className="office-zone-count">查看</span></button>)}</div>
          </div>
          <div className="office-inspector-section office-agent-section"><div className="office-section-heading">Agent 状态 <span>{agentsQuery.isLoading ? '同步中' : `${visibleAgents.length} AGENTS`}</span></div>
            {agentsQuery.isLoading || tasksQuery.isLoading ? <div className="office-no-agents"><span>⌁</span><strong>正在读取 Agent Registry</strong><p>正在同步服务端 Agent 定义与任务执行记录。</p></div> : dataUnavailable ? <div className="office-no-agents"><span>!</span><strong>实时数据暂不可用</strong><p>无法完整读取 Agent Registry 或任务执行记录，请检查服务端 API。</p></div> : visibleAgents.length ? visibleAgents.map((agent) => <button type="button" className={selectedAgentId === agent.agent_id ? 'office-agent-row selected' : 'office-agent-row'} key={agent.agent_id} onClick={() => setSelectedAgentId(agent.agent_id)}><span className={`office-agent-status status-${agent.status}`} /><span><strong>{agent.name}</strong><small>{agent.role} · {statusLabels[agent.status]}</small></span></button>) :
              <div className="office-no-agents"><span>⌁</span><strong>暂无匹配的 Agent</strong><p>当前 Registry 没有匹配的 Agent，或搜索条件没有结果。</p></div>}
          </div>
          <div className="office-selected-desk">{selectedAgent ? <><span>Agent 详情</span><strong>{selectedAgent.name}</strong><small>{selectedAgent.role}</small><small>状态：{statusLabels[selectedAgent.status]}</small><small>工位：{selectedAgent.desk_id || '未分配'}</small><small>任务：{selectedAgent.task_title || selectedAgent.task_id || '无关联任务'}</small><small>Run：{selectedAgent.run_id || '—'}</small><small>模型模板：{selectedAgent.model_template || '—'}</small><small>最近活动：{selectedAgent.last_active_at || '—'}</small><button type="button" className="office-clear-selection" onClick={() => setSelectedAgentId(null)}>关闭 Agent 详情</button></> : selectedDesk ? <><span>当前选中工位</span><strong>{selectedDesk.toUpperCase()}</strong><small>{selectedDeskAgent ? `Agent：${selectedDeskAgent.name} · ${statusLabels[selectedDeskAgent.status]}` : '尚未关联 Agent 数据'}</small></> : selectedZoneDetails ? <><span>区域详情</span><strong>{selectedZoneDetails.name}</strong><small>{selectedZoneDetails.subtitle}</small><small>当前楼层：{floor.name}</small></> : <><span>工位详情</span><strong>选择一个工位或区域</strong><small>点击 3D 场景、区域列表或二维地图查看详情</small></>}</div>
          <p className="office-api-note">状态数据：服务端 Agent Registry + Workbench Tasks API，按 60 秒 / 30 秒轮询；无任务状态的 Agent 标记为未知。仅展示服务端明确提供的 Agent 工位映射，不根据 ID 推断坐标。</p>
        </aside>
      </section>
    </main>
  )
}
