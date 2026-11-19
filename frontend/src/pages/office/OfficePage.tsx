import { useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { apiClient } from '@/lib/api'

type OfficeAgent = { name: string; title: string; desc: string; command: string }
type OfficeTask = { owner: string; db_status: string; state: string }
type OfficeState = {
  generated_at: string
  agents: OfficeAgent[]
  tasks: OfficeTask[]
  llm: { running: string[]; model: string; tokens_today: number }
  metrics: { agents: number; open: number; total: number; done: number }
}
type OfficeResponse = { ok: boolean; data: OfficeState }
type AgentStatus = { label: string; active: boolean; color: string }

const COLORS = { primary: '#2f6af0', text: '#1c2540', muted: '#6b7794', bg: '#aebbd8' }
const DESKS = Array.from({ length: 21 }, (_, index) => ({
  id: index,
  x: -6 + (index % 7) * 2,
  z: -3.4 + Math.floor(index / 7) * 3.4,
}))
const ZONES = [
  { id: 'focus', title: '专注工位', x: -5.1, z: -3.1, w: 5.7, d: 3.1, color: '#eaf0fb' },
  { id: 'studio', title: '团队工作室', x: 1.8, z: -3.1, w: 7.2, d: 3.1, color: '#e7f0f3' },
  { id: 'meeting', title: '协作会议区', x: -5.1, z: 3.2, w: 5.7, d: 3.1, color: '#f2edf9' },
  { id: 'knowledge', title: '知识中心', x: 1.2, z: 3.2, w: 4.1, d: 3.1, color: '#f6f0e8' },
  { id: 'ops', title: '运营中枢', x: 6.4, z: 3.2, w: 2.4, d: 3.1, color: '#eaf2ed' },
]
const statusFor = (agent: OfficeAgent, state: OfficeState | null): AgentStatus => {
  if (!state) return { label: '状态未知', active: false, color: '#9aa9c2' }
  if (state.llm.running.includes(agent.name)) return { label: '运行中', active: true, color: '#36ad78' }
  const task = state.tasks.find((item) => item.owner === agent.name)
  if (task?.db_status === 'todo') return { label: '排队中', active: false, color: '#d9a34e' }
  if (task?.db_status === 'blocked') return { label: '异常', active: false, color: '#d86d78' }
  if (task?.db_status === 'done') return { label: '已完成', active: false, color: '#6b7794' }
  if (task?.db_status === 'doing') return { label: '执行中', active: true, color: '#36ad78' }
  return { label: '暂无活动任务', active: false, color: '#9aa9c2' }
}

function AgentFigure({ agent, index, state }: { agent: OfficeAgent; index: number; state: OfficeState | null }) {
  const headRef = useRef<HTMLDivElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  const phase = useMemo(() => index * 1.731, [index])
  const status = statusFor(agent, state)
  const desk = DESKS[index % DESKS.length]

  useEffect(() => {
    let frame = 0
    const startedAt = performance.now()
    const animate = (now: number) => {
      const t = (now - startedAt) / 1000
      const k = status.active ? 1 : 0.72
      if (headRef.current) {
        const angle = (Math.sin(t * 0.7 + phase) * 2 + Math.sin(t * 0.23 + phase * 2) * 3.2) * k
        headRef.current.style.transform = 'rotate(' + angle + 'deg) translateY(' + Math.sin(t * 1.9 + phase) * 0.5 + 'px)'
      }
      if (bodyRef.current) {
        bodyRef.current.style.transform = 'translateY(' + Math.sin(t * 1.6 + phase) * 0.7 + 'px) scaleY(' + (1 + Math.sin(t * 1.6 + phase) * 0.009) + ')'
      }
      frame = window.requestAnimationFrame(animate)
    }
    frame = window.requestAnimationFrame(animate)
    return () => window.cancelAnimationFrame(frame)
  }, [phase, status.active])

  const left = 50 + desk.x * 3.25
  const top = 48 + desk.z * 3.2
  return (
    <div className="office-agent" style={{ left: left + '%', top: top + '%', zIndex: 20 + Math.round((desk.z + 5) * 10) }}>
      <div className="office-agent-shadow" />
      <div ref={bodyRef} className="office-agent-body">
        <div className="office-agent-torso" style={{ background: status.active ? '#5c8cf5' : '#9aa9c2' }} />
        <div className="office-agent-legs"><i /><i /></div>
      </div>
      <div ref={headRef} className="office-agent-head">
        <div className="office-agent-face"><i /><i /></div>
        <div className="office-agent-hair" />
      </div>
      <div className="office-agent-label">
        <span style={{ background: status.color }} />
        {agent.title || agent.name} · {status.label}
      </div>
    </div>
  )
}

function OfficeScene({ state }: { state: OfficeState | null }) {
  return (
    <div className="office-scene">
      <div className="office-floor" />
      {ZONES.map((zone) => (
        <div key={zone.id} className="office-zone" style={{
          left: 'calc(50% + ' + zone.x * 3.15 + '%)', top: 'calc(50% + ' + zone.z * 3.1 + '%)',
          width: zone.w * 5.2 + '%', height: zone.d * 5.3 + '%', background: zone.color,
        }}>
          <span>{zone.title}</span>
        </div>
      ))}
      {DESKS.map((desk) => (
        <div key={desk.id} className="office-desk" style={{
          left: 'calc(50% + ' + desk.x * 3.25 + '%)', top: 'calc(50% + ' + desk.z * 3.2 + '%)',
        }}>
          <div className="office-desk-top"><i /></div>
          <div className="office-desk-leg a" /><div className="office-desk-leg b" />
          <div className="office-monitor" />
        </div>
      ))}
      {(state?.agents ?? []).slice(0, DESKS.length).map((agent, index) => (
        <AgentFigure key={agent.name} agent={agent} index={index} state={state} />
      ))}
      {!state?.agents.length && <div className="office-empty">等待服务端 Agent 状态数据</div>}
    </div>
  )
}

const shellStyle: CSSProperties = {
  position: 'absolute', left: 205, top: 90, width: 1306, height: 890,
  overflow: 'hidden', borderRadius: 18, background: 'rgba(255,255,255,.9)',
  border: '1px solid rgba(255,255,255,.95)',
  boxShadow: '0 18px 36px -12px rgba(50,80,170,.34), 0 2px 6px rgba(50,80,170,.08)',
  color: COLORS.text, display: 'flex', flexDirection: 'column',
}

const css = `
#cv .office-header{height:78px;flex:0 0 78px;display:flex;align-items:center;justify-content:space-between;padding:0 28px;border-bottom:1px solid #e8edf5;background:rgba(255,255,255,.94)}
#cv .office-eyebrow{font-size:11px;letter-spacing:2px;color:${COLORS.primary};font-weight:800}
#cv .office-title{display:block;margin-top:4px;font-size:19px;font-weight:750}
#cv .office-meta{display:flex;align-items:center;gap:20px;color:${COLORS.muted};font-size:11px}
#cv .office-pill{padding:7px 11px;border-radius:20px;background:#edf3ff;color:${COLORS.primary}}
#cv #pw{position:relative;flex:1;min-height:0;perspective:1200px;transform-style:preserve-3d;overflow:hidden;background:linear-gradient(145deg,#dce5f4,#b8c7e0)}
#cv #stage{position:absolute;inset:0;perspective:1200px;transform-style:preserve-3d;overflow:hidden}
#cv .office-scene{position:absolute;inset:0;transform-style:preserve-3d;perspective:1200px}
#cv .office-floor{position:absolute;width:78%;height:76%;left:11%;top:10%;border-radius:24px;background:linear-gradient(145deg,#f8faff,#e3eaf5);border:1px solid rgba(255,255,255,.95);box-shadow:0 35px 55px rgba(53,76,119,.22);transform:rotateX(57deg) rotateZ(-1deg) translateZ(-35px);transform-style:preserve-3d}
#cv .office-zone{position:absolute;border:1px solid rgba(157,177,210,.65);border-radius:13px;transform:rotateX(57deg) translateZ(-5px);box-shadow:inset 0 0 0 1px rgba(255,255,255,.45);display:flex;align-items:flex-start;justify-content:center;padding-top:8px;color:#8292ad;font-size:10px;letter-spacing:1px;transform-style:preserve-3d}
#cv .office-desk{position:absolute;width:7.2%;height:7.5%;transform:translate(-50%,-50%) rotateX(57deg);transform-style:preserve-3d}
#cv .office-desk-top{position:absolute;inset:15% 4% 35%;border-radius:4px;background:linear-gradient(135deg,#fff,#dce5f2);box-shadow:0 5px 8px rgba(39,60,94,.16);transform:translateZ(24px)}
#cv .office-desk-top i{position:absolute;width:44%;height:36%;left:28%;top:10%;border-radius:2px;background:#314361;box-shadow:inset 0 0 0 2px #8ea1bf}
#cv .office-desk-leg{position:absolute;width:5px;height:28px;bottom:9%;background:#9baac1;transform:translateZ(7px)}
#cv .office-desk-leg.a{left:18%}.office-desk-leg.b{right:18%}
#cv .office-monitor{position:absolute;width:3%;height:8%;left:48%;top:3%;background:#263a5a;transform:translateZ(30px)}
#cv .office-agent{position:absolute;width:54px;height:88px;transform:translate(-50%,-78%);transform-style:preserve-3d}
#cv .office-agent-shadow{position:absolute;width:44px;height:12px;left:5px;bottom:2px;border-radius:50%;background:rgba(40,59,91,.2);filter:blur(4px);transform:rotateX(60deg)}
#cv .office-agent-body{position:absolute;left:15px;top:40px;width:26px;height:38px;transform-origin:50% 100%;transform-style:preserve-3d}
#cv .office-agent-torso{position:absolute;left:1px;top:0;width:24px;height:31px;border-radius:11px 11px 7px 7px;box-shadow:inset -4px 0 rgba(0,0,0,.08)}
#cv .office-agent-legs{position:absolute;left:4px;top:25px;display:flex;gap:7px}
#cv .office-agent-legs i{display:block;width:6px;height:13px;border-radius:4px;background:#465a7a}
#cv .office-agent-head{position:absolute;left:13px;top:12px;width:28px;height:29px;transform-origin:50% 90%;transform-style:preserve-3d}
#cv .office-agent-face{position:absolute;inset:0;border-radius:48% 48% 44% 44%;background:linear-gradient(135deg,#f7d9c1,#dcae91);box-shadow:inset -3px -2px rgba(112,72,54,.12)}
#cv .office-agent-face i{position:absolute;top:13px;width:3px;height:3px;border-radius:50%;background:#34435d}
#cv .office-agent-face i:first-child{left:8px}.office-agent-face i:last-child{right:8px}
#cv .office-agent-hair{position:absolute;left:-1px;top:-2px;width:30px;height:12px;border-radius:15px 15px 5px 5px;background:#35445e}
#cv .office-agent-label{position:absolute;left:50%;top:-11px;transform:translate(-50%,-100%);white-space:nowrap;padding:4px 8px;border-radius:18px;background:rgba(255,255,255,.95);border:1px solid #dce4f1;box-shadow:0 4px 12px rgba(35,60,110,.14);font:600 10px/1.2 -apple-system,BlinkMacSystemFont,sans-serif;color:${COLORS.text}}
#cv .office-agent-label span{display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:5px}
#cv .office-empty{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);padding:12px 18px;border-radius:12px;background:rgba(255,255,255,.9);color:${COLORS.muted};font-size:12px}
#cv .office-hint{position:absolute;left:18px;bottom:16px;z-index:2;padding:9px 12px;border-radius:9px;background:rgba(255,255,255,.86);color:${COLORS.muted};font-size:10px;pointer-events:none}
#cv .office-error{position:absolute;right:18px;bottom:16px;z-index:3;max-width:420px;padding:9px 12px;border-radius:9px;background:#fff4f2;color:#a34b42;font-size:10px}
#cv .office-footer{height:48px;flex:0 0 48px;display:flex;align-items:center;justify-content:space-between;padding:0 24px;border-top:1px solid #e8edf5;color:${COLORS.muted};font-size:10px}
`

export function OfficePage() {
  const [state, setState] = useState<OfficeState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [scale, setScale] = useState(1)
  const [viewport, setViewport] = useState({ width: 1536, height: 1024 })
  const [updatedAt, setUpdatedAt] = useState<string | null>(null)

  useEffect(() => {
    const resize = () => {
      setViewport({ width: window.innerWidth, height: window.innerHeight })
      setScale(Math.min(window.innerWidth / 1536, window.innerHeight / 1024))
    }
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
      <style>{css}</style>
      <div id="cv" style={{
        position: 'absolute', left: 0, top: 0, width: 1536, height: 1024, overflow: 'hidden',
        transformOrigin: '0 0', transform: 'translate(' + (viewport.width - 1536 * scale) / 2 + 'px,' + (viewport.height - 1024 * scale) / 2 + 'px) scale(' + scale + ')',
        background: COLORS.bg, color: COLORS.text,
      }}>
        <section id="off" style={shellStyle}>
          <header className="office-header">
            <div><div className="office-eyebrow">AGENT OFFICE / 3D</div><strong className="office-title">智能体数字办公室</strong></div>
            <div className="office-meta">
              <span>{state?.agents.length ?? '—'} 个 Agent</span><span>{runningCount} 个运行中</span>
              <span className="office-pill">实时状态穿透</span>
            </div>
          </header>
          <div id="pw">
            <div id="stage"><OfficeScene state={state} /></div>
            <div className="office-hint">CSS 3D 空间 · 拖动视角区域以浏览工位 · Agent 持续微动画</div>
            {error && <div className="office-error" role="status">实时状态暂不可用：{error}。场景保留，Agent 状态不使用模拟数据。</div>}
          </div>
          <footer className="office-footer">
            <span>空间布局 · 5 个功能区域 · 21 个工位</span>
            <span>{updatedAt ? '数据更新时间：' + new Date(updatedAt).toLocaleTimeString() : '等待 Office 状态数据'}</span>
          </footer>
        </section>
      </div>
    </main>
  )
}
