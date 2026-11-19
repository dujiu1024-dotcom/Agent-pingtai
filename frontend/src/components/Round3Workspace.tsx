import { Link } from 'react-router-dom'
import './round3-workspace.css'

const quickEntries = [
  { icon: '✳', title: '创建 Agent', detail: '配置智能体与工具能力', to: '/admin/agents', tone: 'blue' },
  { icon: '▤', title: '运行任务', detail: '提交并跟踪执行任务', to: '/workspace/tasks', tone: 'green' },
  { icon: '⌘', title: '管理 Workflow', detail: '编排自动化工作流', to: '/admin/workflows', tone: 'violet' },
]
const agents = [
  { rank: '01', name: '市场分析助手', tone: 'blue' },
  { rank: '02', name: '文档总结专家', tone: 'violet' },
  { rank: '03', name: '代码助手', tone: 'green' },
]
const activity = [
  { time: '—', label: '任务执行完成后，最新动态会显示在这里', tone: 'blue' },
  { time: '—', label: 'Agent 配置更新事件', tone: 'violet' },
  { time: '—', label: 'Workflow 创建与发布事件', tone: 'green' },
]

function KpiCard({ icon, label, unit, tone }: { icon: string; label: string; unit: string; tone: string }) {
  return <article className="r3-kpi-card"><span className={`r3-kpi-icon ${tone}`}>{icon}</span><div className="r3-kpi-label">{label}</div><div className="r3-kpi-value">—<small>{unit}</small></div><span className="r3-kpi-note">等待服务端指标</span></article>
}

function PanelHeading({ title, action }: { title: string; action?: string }) {
  return <div className="r3-panel-heading"><h2>{title}</h2>{action && <Link to="/workspace/tasks">{action} <b>→</b></Link>}</div>
}

export function Round3Home() {
  return (
    <section className="r3-home min-w-0">
      <div className="r3-home-top">
        <div className="r3-home-welcome">
          <div className="r3-home-mark"><span>A</span></div>
          <div className="r3-home-eyebrow">AGENT PLATFORM / WORKSPACE</div>
          <h1>欢迎回来！</h1>
          <p>让 AI 赋能每一个工作场景</p>
          <div className="r3-welcome-rule"/>
        </div>
        <div className="r3-kpi-grid">
          <KpiCard icon="▶" label="今日运行" unit="次" tone="blue"/>
          <KpiCard icon="¥" label="总成本" unit="USD" tone="orange"/>
          <KpiCard icon="✓" label="成功率" unit="%" tone="green"/>
          <KpiCard icon="◔" label="平均耗时" unit="秒" tone="sky"/>
        </div>
      </div>

      <div className="r3-home-middle">
        <section className="r3-quick-stage">
          <div className="r3-stage-orbit orbit-one"/><div className="r3-stage-orbit orbit-two"/>
          <div className="r3-stage-core"><span className="r3-stage-glyph">A</span><b>AI WORKSPACE</b><small>让智能体成为你的团队</small></div>
          <div className="r3-stage-caption"><span/> YOUR AGENT ECOSYSTEM</div>
          <div className="r3-quick-grid">
            {quickEntries.map(item => <Link key={item.title} to={item.to} className={`r3-quick-entry ${item.tone}`}><i>{item.icon}</i><span><b>{item.title}</b><small>{item.detail}</small></span><em>↗</em></Link>)}
          </div>
        </section>

        <section className="r3-task-panel">
          <div className="r3-panel-heading"><div><h2>我的工作台</h2><small>任务状态实时概览</small></div><Link to="/workspace/tasks">⚙ 设置</Link></div>
          <div className="r3-task-tabs"><button className="active">进行中 <b>—</b></button><button>已完成</button><button>待审批 <b>—</b></button></div>
          <div className="r3-task-empty"><span>⌁</span><b>暂无任务记录</b><small>创建任务后，执行进度和审批状态将在这里呈现。</small></div>
          <Link className="r3-task-all" to="/workspace/tasks">查看全部任务 <b>→</b></Link>
        </section>
      </div>

      <div className="r3-home-bottom">
        <section className="r3-home-panel r3-trend-panel">
          <PanelHeading title="系统概览"/>
          <div className="r3-trend-legend"><span><i/>任务数</span><span><i/>成功率</span></div>
          <div className="r3-trend-chart">
            <div className="r3-chart-y"><span>1,500</span><span>1,000</span><span>500</span><span>0</span></div>
            <svg viewBox="0 0 420 145" preserveAspectRatio="none" role="img" aria-label="任务数和成功率趋势暂无数据">
              {[8,48,88,128].map(y=><line key={y} x1="0" y1={y} x2="420" y2={y} stroke="#e4eaf5" strokeDasharray="4 5"/> )}
              <path d="M0 108 C42 99 56 88 92 91 S155 45 190 69 S248 78 280 49 S348 37 420 12" fill="none" stroke="#a9bbf4" strokeWidth="2.5" strokeDasharray="5 7"/>
              <path d="M0 126 C48 112 62 115 100 94 S154 99 190 78 S249 92 280 65 S350 70 420 42" fill="none" stroke="#b7a1fb" strokeWidth="2.5" strokeDasharray="5 7"/>
              <text x="0" y="143" fill="#9aa6ba" fontSize="9">等待趋势 API 数据</text>
            </svg>
          </div>
          <div className="r3-trend-foot"><span><b>—</b> 今日运行</span><span><b>—</b> 已完成</span><span><b>—</b> 进行中</span></div>
        </section>

        <section className="r3-home-panel r3-popular-panel">
          <PanelHeading title="热门 Agent" action="更多"/>
          <div className="r3-popular-list">{agents.map(agent=><div className="r3-popular-row" key={agent.rank}><b className="r3-rank">{agent.rank}</b><span className={`r3-popular-icon ${agent.tone}`}>{agent.tone==='blue'?'▥':agent.tone==='violet'?'▤':'‹›'}</span><strong>{agent.name}</strong><div className="r3-popular-meter"><i className={agent.tone}/></div><small>— 次</small></div>)}</div>
          <p className="r3-data-note">展示角色为原型示例；真实排名待 Agent 统计 API 接入。</p>
        </section>

        <section className="r3-home-panel r3-activity-panel">
          <PanelHeading title="最近动态" action="更多"/>
          <div className="r3-activity-list">{activity.map(item=><div className="r3-activity-row" key={item.label}><i className={item.tone}/><time>{item.time}</time><span>{item.label}</span></div>)}</div>
          <p className="r3-data-note">当前无事件数据</p>
        </section>
      </div>
    </section>
  )
}

export function Round3OfficePage() {
  return <section className="r3-office-page"><div className="r3-office-page-heading"><div><span className="r3-home-eyebrow">DIGITAL WORKSPACE / 3D ENVIRONMENT</span><h1>3D 办公室</h1><p>探索智能体空间，查看楼层、区域与 Agent 实时状态。</p></div><Link to="/workspace/overview">← 返回工作台</Link></div></section>
}
