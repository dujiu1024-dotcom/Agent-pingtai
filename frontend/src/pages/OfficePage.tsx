import { useEffect, useMemo, useState } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { Html, OrbitControls } from '@react-three/drei'
import { apiClient } from '@/lib/api'

type OfficeAgent = {
  name: string
  title: string
  desc: string
  command: string
}
type OfficeRun = {
  id: string
  role: string
  ok: boolean
  error: string | null
  created_at: string | null
}
type OfficeState = {
  generated_at: string
  agents: OfficeAgent[]
  runs: OfficeRun[]
  llm: { running: string[]; model: string; tokens_today: number }
  metrics: { agents: number; open: number; total: number; done: number }
}
type OfficeResponse = { ok: boolean; data: OfficeState }

const COLORS = {
  primary: '#2f6af0',
  text: '#1c2540',
  muted: '#6b7794',
  bg: '#aebbd8',
  card: 'rgba(255,255,255,.9)',
}
const DESKS = Array.from({ length: 21 }, (_, index) => ({
  id: `desk-${String(index + 1).padStart(2, '0')}`,
  x: -7.2 + (index % 7) * 2.35,
  z: -4.6 + Math.floor(index / 7) * 4.5,
}))
const ZONES = [
  { id: 'focus', name: '专注工位', x: -6.1, z: -3.8, width: 5.4, depth: 4.5, color: '#eaf0fb' },
  { id: 'studio', name: '团队工作室', x: 1.1, z: -3.8, width: 7.2, depth: 4.5, color: '#e7f0f3' },
  { id: 'meeting', name: '协作会议区', x: -6.1, z: 3.2, width: 5.4, depth: 4.7, color: '#f2edf9' },
  { id: 'knowledge', name: '知识中心', x: 1.1, z: 3.2, width: 3.5, depth: 4.7, color: '#f6f0e8' },
  { id: 'ops', name: '运营中枢', x: 5.8, z: 3.2, width: 2.5, depth: 4.7, color: '#eaf2ed' },
]
const statusLabel = (running: boolean) => running ? '运行中' : '待机'
const panelStyle: React.CSSProperties = {
  position: 'absolute',
  background: COLORS.card,
  border: '1px solid rgba(255,255,255,.95)',
  borderRadius: 16,
  boxShadow: '0 18px 36px -12px rgba(50,80,170,.34), 0 2px 6px rgba(50,80,170,.08)',
  color: COLORS.text,
}

function AnimatedAgent({ agent, index, running }: { agent: OfficeAgent; index: number; running: boolean }) {
  const head = useMemo(() => ({ current: null as THREE.Group | null }), [])
  const body = useMemo(() => ({ current: null as THREE.Group | null }), [])
  const position: [number, number, number] = [
    DESKS[index % DESKS.length].x,
    0,
    DESKS[index % DESKS.length].z + 0.95,
  ]
  useFrame(({ clock }) => {
    const time = clock.elapsedTime
    if (head.current) head.current.rotation.z = Math.sin(time * 0.7 + index) * 0.0349
    if (body.current) body.current.position.y = 0.78 + Math.sin(time * 1.1 + index) * 0.035
  })
  return (
    <group position={position}>
      <group ref={body}>
        <mesh position={[0, 0.78, 0]} castShadow>
          <capsuleGeometry args={[0.22, 0.48, 4, 8]} />
          <meshStandardMaterial color={running ? '#5c8cf5' : '#9aa9c2'} roughness={0.68} />
        </mesh>
      </group>
      <group ref={head} position={[0, 1.35, 0]}>
        <mesh castShadow>
          <sphereGeometry args={[0.2, 18, 14]} />
          <meshStandardMaterial color="#f0c9aa" roughness={0.8} />
        </mesh>
        <mesh position={[0, 0.12, -0.025]}>
          <sphereGeometry args={[0.205, 18, 10, 0, Math.PI * 2, 0, Math.PI * 0.45]} />
          <meshStandardMaterial color="#35445e" roughness={0.95} />
        </mesh>
        <Html position={[0, 0.48, 0]} center distanceFactor={11} occlude={false}>
          <div style={{
            whiteSpace: 'nowrap', padding: '4px 9px', borderRadius: 20,
            background: 'rgba(255,255,255,.94)', border: `1px solid ${running ? '#a9c2ff' : '#dce2ed'}`,
            boxShadow: '0 4px 12px rgba(35,60,110,.14)', color: COLORS.text,
            font: '600 10px/1.2 -apple-system,BlinkMacSystemFont,sans-serif',
          }}>
            <span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', marginRight: 5, background: running ? '#36ad78' : '#9aa9c2' }} />
            {agent.title || agent.name} · {statusLabel(running)}
          </div>
        </Html>
      </group>
    </group>
  )
}

function OfficeStage({ state }: { state: OfficeState | null }) {
  const running = new Set(state?.llm.running ?? [])
  return (
    <>
      <color attach="background" args={[COLORS.bg]} />
      <fog attach="fog" args={[COLORS.bg, 20, 36]} />
      <ambientLight intensity={1.15} />
      <hemisphereLight args={['#ffffff', '#7184a5', 0.65]} />
      <directionalLight position={[8, 15, 9]} intensity={2.1} castShadow />
      <group position={[0, -0.18, 0]}>
        <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
          <planeGeometry args={[21, 14]} />
          <meshStandardMaterial color="#e7edf7" roughness={0.92} />
        </mesh>
        <gridHelper args={[21, 21, '#c1cde0', '#d4ddeb']} position={[0, 0.012, 0]} />
        {ZONES.map((zone) => (
          <group key={zone.id} position={[zone.x, 0.025, zone.z]}>
            <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
              <planeGeometry args={[zone.width, zone.depth]} />
              <meshStandardMaterial color={zone.color} roughness={0.95} />
            </mesh>
            <mesh position={[0, 0.05, -zone.depth / 2 + 0.12]}>
              <boxGeometry args={[zone.width, 0.08, 0.12]} />
              <meshStandardMaterial color="#c5d3eb" />
            </mesh>
          </group>
        ))}
        {DESKS.map((desk) => (
          <group key={desk.id} position={[desk.x, 0, desk.z]}>
            <mesh position={[0, 0.58, 0]} castShadow>
              <boxGeometry args={[1.35, 0.12, 0.82]} />
              <meshStandardMaterial color="#f7f9fd" roughness={0.7} />
            </mesh>
            {([-0.55, 0.55] as const).flatMap((x) => ([-0.27, 0.27] as const).map((z) => (
              <mesh key={`${x}-${z}`} position={[x, 0.29, z]}>
                <boxGeometry args={[0.07, 0.55, 0.07]} />
                <meshStandardMaterial color="#9aa9c2" />
              </mesh>
            )))}
            <mesh position={[0, 0.9, -0.17]}>
              <boxGeometry args={[0.62, 0.42, 0.07]} />
              <meshStandardMaterial color="#243654" />
            </mesh>
          </group>
        ))}
        {(state?.agents ?? []).slice(0, DESKS.length).map((agent, index) => (
          <AnimatedAgent key={agent.name} agent={agent} index={index} running={running.has(agent.name)} />
        ))}
      </group>
      <OrbitControls makeDefault minDistance={10} maxDistance={27} maxPolarAngle={Math.PI / 2.08}
        minPolarAngle={0.35} enableDamping dampingFactor={0.08} target={[0, 0.2, 0]} />
    </>
  )
}

export function OfficePage() {
  const [state, setState] = useState<OfficeState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [scale, setScale] = useState(1)
  const [updatedAt, setUpdatedAt] = useState<string | null>(null)

  useEffect(() => {
    const resize = () => setScale(Math.min(window.innerWidth / 1536, window.innerHeight / 1024))
    resize()
    window.addEventListener('resize', resize)
    return () => window.removeEventListener('resize', resize)
  }, [])

  useEffect(() => {
    let active = true
    const load = async () => {
      try {
        const response = await apiClient.get<OfficeResponse>('/platform/v1/office/state', { etag: false, timeoutMs: 12000 })
        if (!active) return
        setState(response.data)
        setError(null)
        setUpdatedAt(response.data.generated_at)
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : 'Office 状态读取失败')
      }
    }
    void load()
    const timer = window.setInterval(() => void load(), 10000)
    return () => { active = false; window.clearInterval(timer) }
  }, [])

  const runningCount = state?.llm.running.length ?? 0
  return (
    <main style={{ position: 'fixed', inset: 0, overflow: 'hidden', background: COLORS.bg, fontFamily: '-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif' }}>
      <div id="cv" style={{ position: 'absolute', left: 0, top: 0, width: 1536, height: 1024, overflow: 'hidden', transformOrigin: '0 0', transform: `scale(${scale})`, background: COLORS.bg, color: COLORS.text }}>
        <div id="off" style={{ ...panelStyle, left: 205, top: 90, width: 1306, height: 890, overflow: 'hidden', perspective: 1200, display: 'flex', flexDirection: 'column' }}>
          <div style={{ height: 78, flex: '0 0 78px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 28px', borderBottom: '1px solid #e8edf5', background: 'rgba(255,255,255,.94)' }}>
            <div><div style={{ fontSize: 11, letterSpacing: 2, color: COLORS.primary, fontWeight: 800 }}>AGENT OFFICE / 3D</div><strong style={{ display: 'block', marginTop: 4, fontSize: 19 }}>智能体数字办公室</strong></div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 20, color: COLORS.muted, fontSize: 11 }}>
              <span>{state?.agents.length ?? '—'} 个 Agent</span><span>{runningCount} 个运行中</span>
              <span style={{ padding: '7px 11px', borderRadius: 20, background: '#edf3ff', color: COLORS.primary }}>实时状态穿透</span>
            </div>
          </div>
          <div id="pw" style={{ position: 'relative', flex: 1, minHeight: 0, transformStyle: 'preserve-3d', perspective: 1200 }}>
            <div id="stage" style={{ position: 'absolute', inset: 0 }}>
              <Canvas shadows dpr={[1, 1.6]} camera={{ position: [13, 12, 16], fov: 38 }}>
                <OfficeStage state={state} />
              </Canvas>
            </div>
            <div style={{ position: 'absolute', left: 18, bottom: 16, zIndex: 2, padding: '9px 12px', borderRadius: 9, background: 'rgba(255,255,255,.86)', color: COLORS.muted, fontSize: 10, pointerEvents: 'none' }}>
              拖拽旋转 · 滚轮缩放 · Agent 头部与身体持续微动画
            </div>
            {error && <div role="status" style={{ position: 'absolute', right: 18, bottom: 16, zIndex: 3, maxWidth: 420, padding: '9px 12px', borderRadius: 9, background: '#fff4f2', color: '#a34b42', fontSize: 10 }}>实时状态暂不可用：{error}。场景保留，Agent 状态不使用模拟数据。</div>}
          </div>
          <footer style={{ height: 48, flex: '0 0 48px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 24px', borderTop: '1px solid #e8edf5', color: COLORS.muted, fontSize: 10 }}>
            <span>空间布局 · 5 个功能区域 · 21 个工位</span><span>{updatedAt ? `数据更新时间：${new Date(updatedAt).toLocaleTimeString()}` : '等待 Office 状态数据'}</span>
          </footer>
        </div>
      </div>
    </main>
  )
}
