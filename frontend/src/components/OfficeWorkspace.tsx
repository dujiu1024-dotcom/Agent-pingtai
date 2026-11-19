import { useMemo, useState } from 'react'
import { Canvas } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import './office-workspace.css'

type Floor = { id: string; label: string; name: string; description: string }
type AgentStatus = 'idle' | 'queued' | 'running' | 'collaborating' | 'waiting_approval' | 'paused' | 'failed' | 'offline'
type OfficeAgent = {
  agent_id: string
  name: string
  role: string
  status: AgentStatus
  floor_id: string
  desk_id?: string
}

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
}

// The office API is the source of truth. Keep this empty until a real snapshot is supplied;
// never turn illustrative scene objects into fabricated live Agent telemetry.
const agents: OfficeAgent[] = []

const zones = [
  { id: 'focus', name: '专注工位', subtitle: 'FOCUS PODS', x: -6.1, z: -3.8, width: 5.4, depth: 4.5 },
  { id: 'studio', name: '团队工作室', subtitle: 'TEAM STUDIO', x: 1.1, z: -3.8, width: 7.2, depth: 4.5 },
  { id: 'meeting', name: '协作会议区', subtitle: 'MEETING HUB', x: -6.1, z: 3.2, width: 5.4, depth: 4.7 },
  { id: 'knowledge', name: '知识中心', subtitle: 'KNOWLEDGE', x: 1.1, z: 3.2, width: 3.5, depth: 4.7 },
  { id: 'ops', name: '运营中枢', subtitle: 'OPS CENTER', x: 5.8, z: 3.2, width: 2.5, depth: 4.7 },
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
      {[[-0.55, -0.27], [0.55, -0.27], [-0.55, 0.27], [0.55, 0.27]].map(([dx, dz]) => (
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

function OfficeScene({ selectedDesk, onSelectDesk }: { selectedDesk: string | null; onSelectDesk: (id: string) => void }) {
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
          <group key={zone.id} position={[zone.x, 0.025, zone.z]}>
            <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
              <planeGeometry args={[zone.width, zone.depth]} />
              <meshStandardMaterial color="#f4f7fc" roughness={0.95} />
            </mesh>
            <mesh position={[0, 0.035, -zone.depth / 2 + 0.12]}>
              <boxGeometry args={[zone.width, 0.06, 0.12]} />
              <meshStandardMaterial color="#d2def3" />
            </mesh>
          </group>
        ))}
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
  const [search, setSearch] = useState('')
  const floor = useMemo(() => floors.find((item) => item.id === floorId) ?? floors[1], [floorId])
  const visibleAgents = agents.filter((agent) => agent.floor_id === floorId &&
    `${agent.name} ${agent.agent_id} ${agent.desk_id ?? ''}`.toLowerCase().includes(search.toLowerCase()))

  return (
    <main className="office-workspace">
      <header className="office-topbar">
        <div className="office-brand"><span className="office-brand-mark">A</span><span><strong>AGENT OFFICE</strong><small>智能体数字办公空间</small></span></div>
        <div className="office-floor-tabs" role="group" aria-label="楼层导航">
          {floors.map((item) => <button key={item.id} className={item.id === floorId ? 'office-floor-tab active' : 'office-floor-tab'}
            onClick={() => { setFloorId(item.id); setSelectedDesk(null) }} aria-pressed={item.id === floorId}>
            <strong>{item.label}</strong><span>{item.name}</span>
          </button>)}
        </div>
        <div className="office-top-actions">
          <span className="office-sync-state"><i />实时数据待接入</span>
          <button className={view === 'space' ? 'office-view-button active' : 'office-view-button'} onClick={() => setView('space')}>空间视角</button>
          <button className={view === 'top' ? 'office-view-button active' : 'office-view-button'} onClick={() => setView('top')}>俯视图</button>
        </div>
      </header>

      <section className="office-heading">
        <div><p className="office-eyebrow">DIGITAL WORKSPACE / {floor.label}F</p><h1>{floor.name}</h1><p>{floor.description} <span>·</span> 交互式 3D 空间</p></div>
        <div className="office-heading-stats"><div><strong>{desks.length}</strong><span>工位总数</span></div><i /><div><strong>{visibleAgents.length}</strong><span>在线 Agent</span></div><i /><div><strong>—</strong><span>执行中任务</span></div></div>
      </section>

      <section className="office-main-grid">
        <div className="office-viewport">
          <div className="office-viewport-caption"><span><i /> 3D SPACE</span><span>拖拽旋转 · 滚轮缩放 · 点击工位查看</span></div>
          <div className={view === 'top' ? 'office-canvas office-canvas-top' : 'office-canvas'}>
            <Canvas shadows dpr={[1, 1.6]} camera={{ position: view === 'top' ? [0, 22, 0.01] : [13, 12, 16], fov: view === 'top' ? 42 : 38 }}>
              <OfficeScene selectedDesk={selectedDesk} onSelectDesk={setSelectedDesk} />
            </Canvas>
          </div>
          <div className="office-map-legend"><span><i className="legend-desk" />标准工位</span><span><i className="legend-selected" />已选工位</span><span><i className="legend-agent" />Agent 状态（接入后显示）</span><small>场景布局为交互式空间模型，Agent 状态以服务端快照为准</small></div>
        </div>

        <aside className="office-inspector">
          <div className="office-inspector-title"><div><p className="office-eyebrow">SPACE EXPLORER</p><h2>空间信息</h2></div><span className="office-floor-chip">{floor.label}F</span></div>
          <label className="office-search"><span>⌕</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索 Agent / 工位" aria-label="搜索 Agent 或工位" /></label>
          <div className="office-inspector-section"><div className="office-section-heading">楼层概览 <span>OVERVIEW</span></div>
            <div className="office-floor-overview"><div><strong>21</strong><span>工位</span></div><div><strong>05</strong><span>功能区域</span></div><div><strong>{visibleAgents.length.toString().padStart(2, '0')}</strong><span>Agent</span></div></div>
          </div>
          <div className="office-inspector-section"><div className="office-section-heading">功能区域 <span>5 ZONES</span></div>
            <div className="office-zone-list">{zones.map((zone) => <div className="office-zone-row" key={zone.id}><span className="office-zone-dot" /><span><strong>{zone.name}</strong><small>{zone.subtitle}</small></span><span className="office-zone-count">—</span></div>)}</div>
          </div>
          <div className="office-inspector-section office-agent-section"><div className="office-section-heading">Agent 状态 <span>{visibleAgents.length} ONLINE</span></div>
            {visibleAgents.length ? visibleAgents.map((agent) => <div className="office-agent-row" key={agent.agent_id}><span className={`office-agent-status status-${agent.status}`} /><span><strong>{agent.name}</strong><small>{agent.role} · {statusLabels[agent.status]}</small></span></div>) :
              <div className="office-no-agents"><span>⌁</span><strong>暂无实时 Agent 数据</strong><p>Office snapshot 接入后，将在此展示 Agent、当前任务与实时状态。</p></div>}
          </div>
          <div className="office-selected-desk">{selectedDesk ? <><span>当前选中工位</span><strong>{selectedDesk.toUpperCase()}</strong><small>尚未分配 Agent</small></> : <><span>工位详情</span><strong>选择一个工位</strong><small>点击 3D 场景中的桌面查看详情</small></>}</div>
          <p className="office-api-note">状态数据：服务端 Office snapshot / event stream。未接入前不展示模拟运行状态。</p>
        </aside>
      </section>
    </main>
  )
}
