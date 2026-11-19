import { useMemo, useRef } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { Html, OrbitControls } from '@react-three/drei'
import type { Group } from 'three'

type Zone = { id: string; label: string; description: string }
type Agent = { agent_id: string; name: string; status: string }

const zoneLayout = [
  { id: 'open-work', x: -4.1, z: -1.25, w: 5.5, d: 3.2, tint: '#dce8ff', desks: 4 },
  { id: 'team-studios', x: 3.2, z: -1.3, w: 4.2, d: 3.2, tint: '#e9e2ff', desks: 3 },
  { id: 'meeting-hub', x: -3.8, z: 3.0, w: 4.2, d: 2.4, tint: '#dff5ee', desks: 1 },
  { id: 'knowledge-center', x: 1.0, z: 3.0, w: 3.5, d: 2.4, tint: '#fff0dc', desks: 2 },
  { id: 'operations-center', x: 5.0, z: 3.0, w: 2.3, d: 2.4, tint: '#e4edf6', desks: 1 },
]

function Desk({ position, accent = '#6d8ff5' }: { position: [number, number, number]; accent?: string }) {
  return (
    <group position={position}>
      <mesh position={[0, 0.46, 0]} castShadow receiveShadow>
        <boxGeometry args={[1.05, 0.12, 0.62]} />
        <meshStandardMaterial color="#f8fafc" roughness={0.62} />
      </mesh>
      {[[-0.42, 0.21, -0.22], [0.42, 0.21, -0.22], [-0.42, 0.21, 0.22], [0.42, 0.21, 0.22]].map((p, i) => (
        <mesh key={i} position={p as [number, number, number]} castShadow>
          <boxGeometry args={[0.055, 0.42, 0.055]} />
          <meshStandardMaterial color="#a9b5c8" />
        </mesh>
      ))}
      <mesh position={[0, 0.82, -0.19]} castShadow>
        <boxGeometry args={[0.48, 0.36, 0.045]} />
        <meshStandardMaterial color="#263653" roughness={0.32} />
      </mesh>
      <mesh position={[0, 0.82, -0.163]}>
        <planeGeometry args={[0.4, 0.27]} />
        <meshStandardMaterial color={accent} emissive={accent} emissiveIntensity={0.32} />
      </mesh>
      <mesh position={[0.0, 0.53, 0.13]}>
        <boxGeometry args={[0.22, 0.025, 0.14]} />
        <meshStandardMaterial color="#dce3ef" />
      </mesh>
    </group>
  )
}

function Chair({ position }: { position: [number, number, number] }) {
  return (
    <group position={position}>
      <mesh position={[0, 0.34, 0]} castShadow>
        <boxGeometry args={[0.48, 0.1, 0.46]} />
        <meshStandardMaterial color="#91a5c7" />
      </mesh>
      <mesh position={[0, 0.62, -0.18]} castShadow>
        <boxGeometry args={[0.48, 0.5, 0.09]} />
        <meshStandardMaterial color="#7188b0" />
      </mesh>
      <mesh position={[0, 0.16, 0]}>
        <cylinderGeometry args={[0.035, 0.035, 0.3, 8]} />
        <meshStandardMaterial color="#a9b6c9" />
      </mesh>
      <mesh position={[0, 0.045, 0]}>
        <cylinderGeometry args={[0.23, 0.23, 0.045, 12]} />
        <meshStandardMaterial color="#b9c4d5" />
      </mesh>
    </group>
  )
}

function ZoneRoom({ zone, selected, onSelect }: { zone: (typeof zoneLayout)[number]; selected: boolean; onSelect: () => void }) {
  const deskPositions = useMemo(() => {
    const count = zone.desks
    return Array.from({ length: count }, (_, i) => {
      const cols = count > 2 ? 2 : count
      const rows = Math.ceil(count / cols)
      const col = i % cols
      const row = Math.floor(i / cols)
      return [
        zone.x - ((cols - 1) * 1.25) / 2 + col * 1.25,
        0.08,
        zone.z - ((rows - 1) * 1.2) / 2 + row * 1.2,
      ] as [number, number, number]
    })
  }, [zone])
  return (
    <group>
      <mesh position={[zone.x, 0.035, zone.z]} receiveShadow onClick={onSelect}>
        <boxGeometry args={[zone.w, 0.07, zone.d]} />
        <meshStandardMaterial color={selected ? '#b8ccff' : zone.tint} roughness={0.9} />
      </mesh>
      <mesh position={[zone.x, 0.09, zone.z]} onClick={onSelect}>
        <boxGeometry args={[zone.w, 0.025, zone.d]} />
        <meshStandardMaterial color={selected ? '#7399f5' : '#ffffff'} transparent opacity={selected ? 0.24 : 0.1} />
      </mesh>
      {deskPositions.map((p, i) => (
        <group key={i}>
          <Desk position={p} accent={selected ? '#4778ee' : '#8199cb'} />
          <Chair position={[p[0], 0, p[2] + 0.58]} />
        </group>
      ))}
      <Html position={[zone.x, 0.2, zone.z - zone.d / 2 + 0.25]} center transform distanceFactor={10}>
        <div className="r3-scene-label">{zone.id === 'open-work' ? 'OPEN WORKSPACE' : zone.id === 'team-studios' ? 'TEAM STUDIO' : zone.id === 'meeting-hub' ? 'MEETING HUB' : zone.id === 'knowledge-center' ? 'KNOWLEDGE' : 'OPERATIONS'}</div>
      </Html>
    </group>
  )
}

function AgentFigure({ agent, index }: { agent: Agent; index: number }) {
  const group = useRef<Group>(null)
  const color = agent.status === 'running' ? '#2f6af0' : '#4caf91'
  useFrame(({ clock }) => {
    if (group.current) group.current.position.y = 0.04 + Math.sin(clock.elapsedTime * 2 + index) * 0.035
  })
  return (
    <group ref={group} position={[-5.1 + (index % 7) * 1.65, 0, 4.1 + Math.floor(index / 7) * 0.55]}>
      <mesh position={[0, 0.88, 0]} castShadow><capsuleGeometry args={[0.19, 0.42, 4, 8]} /><meshStandardMaterial color={color} roughness={0.5} /></mesh>
      <mesh position={[0, 1.23, 0]} castShadow><sphereGeometry args={[0.16, 16, 16]} /><meshStandardMaterial color="#f1c9a8" /></mesh>
      <mesh position={[0, 1.48, 0]}><sphereGeometry args={[0.055, 12, 12]} /><meshBasicMaterial color={color} /></mesh>
      <Html position={[0, 1.78, 0]} center distanceFactor={10} style={{ pointerEvents: 'none' }}>
        <div className="r3-agent-tag"><b>{agent.name}</b><span>{agent.status === 'running' ? '运行中' : '空闲'} · 未分配工位</span></div>
      </Html>
    </group>
  )
}

function OfficeModel({ zones, selectedZone, onSelectZone, agents }: { zones: Zone[]; selectedZone: string | null; onSelectZone: (id: string) => void; agents: Agent[] }) {
  return (
    <>
      <color attach="background" args={['#e9eff9']} />
      <fog attach="fog" args={['#e9eff9', 18, 32]} />
      <ambientLight intensity={1.5} />
      <hemisphereLight args={['#ffffff', '#91a5c5', 1.2]} />
      <directionalLight position={[7, 12, 5]} intensity={2.2} castShadow shadow-mapSize={[2048, 2048]} />
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.14, 0]} receiveShadow>
        <planeGeometry args={[24, 18]} />
        <meshStandardMaterial color="#dce5f2" roughness={1} />
      </mesh>
      <mesh position={[0, -0.04, 0]} receiveShadow>
        <boxGeometry args={[15.2, 0.18, 10.4]} />
        <meshStandardMaterial color="#f7f9fd" roughness={0.86} />
      </mesh>
      <mesh position={[0, 1.65, -5.05]} castShadow>
        <boxGeometry args={[15.2, 3.3, 0.16]} />
        <meshStandardMaterial color="#d9e2f0" transparent opacity={0.42} />
      </mesh>
      <mesh position={[-7.52, 1.65, 0]} castShadow>
        <boxGeometry args={[0.16, 3.3, 10.2]} />
        <meshStandardMaterial color="#d9e2f0" transparent opacity={0.34} />
      </mesh>
      {zoneLayout.filter((layout) => zones.some((zone) => zone.id === layout.id)).map((zone) => (
        <ZoneRoom key={zone.id} zone={zone} selected={selectedZone === zone.id} onSelect={() => onSelectZone(zone.id)} />
      ))}
      {agents.map((agent, index) => <AgentFigure key={agent.agent_id} agent={agent} index={index} />)}
      <Html position={[0, 0.2, -4.7]} center transform distanceFactor={10}><div className="r3-scene-title">AGENT PLATFORM · DIGITAL OFFICE</div></Html>
      {agents.length > 0 && <Html position={[0, 0.2, 4.8]} center transform distanceFactor={10}><div className="r3-scene-live-label">LIVE AGENTS · LOCATION NOT PROVIDED</div></Html>}
      <OrbitControls makeDefault minDistance={10} maxDistance={24} maxPolarAngle={Math.PI / 2.08} minPolarAngle={0.55} target={[0, 0.5, 0]} enableDamping dampingFactor={0.08} />
    </>
  )
}

export function OfficeScene3D({ zones, selectedZone, onSelectZone, agents }: { zones: Zone[]; selectedZone: string | null; onSelectZone: (id: string) => void; agents: Agent[] }) {
  return (
    <div className="r3-office-canvas">
      <Canvas shadows dpr={[1, 1.6]} camera={{ position: [12, 12, 15], fov: 38 }} gl={{ antialias: true, alpha: false }}>
        <OfficeModel zones={zones} selectedZone={selectedZone} onSelectZone={onSelectZone} agents={agents} />
      </Canvas>
      <div className="r3-canvas-hint"><span>拖动旋转</span><i/>滚轮缩放<i/>点击区域查看详情</div>
    </div>
  )
}
