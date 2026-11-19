import { useEffect, useRef } from 'react'

/**
 * Round 3 Office 3D 办公室 — 纯 CSS 3D 版（照原型重构）
 * 原型规格: #off 1306×890 left=205 top=90 + #pw perspective + Agent rotate/translate
 */
export function OfficePage() {
  const rafRef = useRef<number | null>(null)

  useEffect(() => {
    let start = performance.now()

    function animate() {
      const t = (performance.now() - start) / 1000
      const agents = document.querySelectorAll<HTMLElement>('.r3-agent')
      agents.forEach((agent, i) => {
        const phase = i * 0.7
        const head = agent.querySelector<HTMLElement>('.r3-agent-head')
        const body = agent.querySelector<HTMLElement>('.r3-agent-body')
        const bob = agent.querySelector<HTMLElement>('.r3-agent-bob')
        const k = 0.6 + (i % 3) * 0.1
        const p = phase
        if (head) {
          const rx = Math.sin(t * 0.7 + p) * 4 + Math.sin(t * 0.23 + p * 2) * 2
          head.style.transform = `rotateY(${rx * k}deg) rotateX(${Math.sin(t * 0.5 + p) * 3}deg)`
        }
        if (body) {
          body.style.transform = `translateY(${Math.sin(t * 0.9 + p) * 1.5}px)`
        }
        if (bob) {
          bob.style.transform = `translateY(${Math.sin(t * 0.6 + p) * 3}px)`
        }
      })
      rafRef.current = requestAnimationFrame(animate)
    }

    rafRef.current = requestAnimationFrame(animate)
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
    }
  }, [])

  const officeAgents = [
    { name: 'Agent-01', role: '代码助手', x: 180, y: 220, color: '#2f6af0' },
    { name: 'Agent-02', role: '市场分析', x: 480, y: 220, color: '#7c3aed' },
    { name: 'Agent-03', role: '文档专家', x: 780, y: 220, color: '#059669' },
    { name: 'Agent-04', role: '数据分析', x: 280, y: 500, color: '#d97706' },
    { name: 'Agent-05', role: '运营中心', x: 680, y: 500, color: '#dc2626' },
  ]

  return (
    <main className="office-page fixed inset-0 z-20 overflow-hidden">
      <div
        id="off"
        className="office-container absolute"
        style={{ left: 205, top: 90, width: 1306, height: 890 }}
      >
        {/* 背景顶栏 */}
        <header className="office-header">
          <div>
            <div className="office-kicker">DIGITAL WORKSPACE</div>
            <h1>3D 数字办公室</h1>
            <p>探索智能体工作空间，查看各区域与 Agent 实时状态</p>
          </div>
          <div className="office-status">
            <span className="status-dot" /> 实时状态待连接
          </div>
        </header>

        {/* 3D 舞台 */}
        <div id="pw" className="office-perspective">
          <div id="stage" className="office-stage">
            {/* 地板网格 */}
            <div className="office-floor" aria-hidden="true" />

            {/* Agent 阵列 */}
            {officeAgents.map((a) => (
              <div
                key={a.name}
                className="r3-agent absolute"
                style={{ left: a.x, top: a.y }}
              >
                <div className="r3-agent-bob">
                  <div className="r3-agent-body" style={{ background: a.color }}>
                    <div className="r3-agent-head">
                      <span className="eye left" />
                      <span className="eye right" />
                    </div>
                  </div>
                </div>
                <div className="r3-agent-label">
                  <strong>{a.name}</strong>
                  <span>{a.role}</span>
                </div>
                <span className="r3-agent-status online" aria-label="在线" />
              </div>
            ))}

            {/* 区域标签 */}
            <div className="office-zone" style={{ left: 120, top: 120 }}>
              <strong>协作区</strong>
              <span>COLLABORATION</span>
            </div>
            <div className="office-zone" style={{ left: 580, top: 120 }}>
              <strong>知识中心</strong>
              <span>KNOWLEDGE</span>
            </div>
            <div className="office-zone" style={{ left: 120, top: 420 }}>
              <strong>专注工位</strong>
              <span>FOCUS PODS</span>
            </div>
            <div className="office-zone" style={{ left: 580, top: 420 }}>
              <strong>运营中枢</strong>
              <span>OPS CENTER</span>
            </div>
          </div>
        </div>
      </div>
    </main>
  )
}
