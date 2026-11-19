import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiClient } from '@/lib/api/api-client'
import './round3-workspace.css'

type WorkspaceSnapshot = {
  generated_at: string
  metrics: { total: number; open: number; closed: number; done: number; agents: number }
  agents: { name: string; title: string; desc: string; command: string }[]
  llm: { running: string[]; tokens_today: number }
  tasks: { id: string; title: string; state: string; owner: string; db_status: string }[]
  events: { list: { at: string | null; type: string; text: string; task: string }[] }
}
type WorkspaceEnvelope = { ok: boolean; data: WorkspaceSnapshot }

const quickEntries = [
  { icon: '✳', title: '创建 Agent', detail: '配置智能体与工具能力', to: '/admin/agents', tone: 'blue' },
  { icon: '▤', title: '运行任务', detail: '提交并跟踪执行任务', to: '/workspace/tasks', tone: 'green' },
  { icon: '⌘', title: '管理 Workflow', detail: '编排自动化工作流', to: '/admin/workflows', tone: 'violet' },
]

function KpiCard({ icon, label, unit, tone, value, note }: { icon: string; label: string; unit: string; tone: string; value: string | number; note: string }) {
  return <article className="r3-kpi-card"><span className={`r3-kpi-icon ${tone}`}>{icon}</span><div className="r3-kpi-label">{label}</div><div className="r3-kpi-value">{value}<small>{unit}</small></div><span className="r3-kpi-note">{note}</span></article>
}

function PanelHeading({ title, action, to = '/workspace/tasks' }: { title: string; action?: string; to?: string }) {
  return <div className="r3-panel-heading"><h2>{title}</h2>{action && <Link to={to}>{action} <b>→</b></Link>}</div>
}

function formatTime(value: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export function Round3Home() {
  const [snapshot, setSnapshot] = useState<WorkspaceSnapshot | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    const load = async () => {
      try {
        const response = await apiClient.get<WorkspaceEnvelope>('/platform/v1/office/state')
        if (!active) return
        if (!response?.ok || !response.data) throw new Error('工作台数据响应格式无效')
        setSnapshot(response.data)
        setError(null)
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : '工作台数据读取失败')
      } finally {
        if (active) setLoading(false)
      }
    }
    void load()
    const poll = window.setInterval(() => void load(), 15000)
    return () => { active = false; window.clearInterval(poll) }
  }, [])

  const total = snapshot?.metrics.total ?? 0
  const successRate = total > 0 ? `${((snapshot!.metrics.done / total) * 100).toFixed(1)}` : '—'
  const tasks = snapshot?.tasks.slice(0, 3) ?? []
  const popularAgents = snapshot?.agents.slice(0, 3) ?? []
  const activities = snapshot?.events.list.slice(0, 4) ?? []

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
          <KpiCard icon="▶" label="累计任务" unit="次" tone="blue" value={snapshot ? snapshot.metrics.total.toLocaleString() : '—'} note="来自 Office state API"/>
          <KpiCard icon="¥" label="总成本" unit="USD" tone="orange" value="—" note="成本汇总 API 尚未提供"/>
          <KpiCard icon="✓" label="成功率" unit="%" tone="green" value={successRate} note="成功任务 / 累计任务"/>
          <KpiCard icon="◔" label="平均耗时" unit="秒" tone="sky" value="—" note="耗时聚合 API 尚未提供"/>
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
          <div className="r3-task-tabs"><button className="active">全部任务 <b>{snapshot ? snapshot.metrics.total : '—'}</b></button><button>运行中 <b>{snapshot ? snapshot.metrics.open : '—'}</b></button><button>待审批</button></div>
          {tasks.length ? <div className="r3-task-list">{tasks.map(task=><Link to={`/operations/tasks/${task.id}`} className="r3-task-row" key={task.id}><span className={task.db_status==='done'?'done':task.db_status==='blocked'?'blocked':'running'}/><span><b>{task.title}</b><small>{task.owner} · {task.state}</small></span><i>›</i></Link>)}</div> : <div className="r3-task-empty"><span>⌁</span><b>{loading ? '正在读取任务…' : '暂无任务记录'}</b><small>{error ?? '创建任务后，执行进度和审批状态将在这里呈现。'}</small></div>}
          <Link className="r3-task-all" to="/workspace/tasks">查看全部任务 <b>→</b></Link>
        </section>
      </div>

      <div className="r3-home-bottom">
        <section className="r3-home-panel r3-trend-panel">
          <PanelHeading title="系统概览"/>
          <div className="r3-trend-legend"><span><i/>任务数</span><span><i/>成功率</span></div>
          <div className="r3-trend-chart">
            <div className="r3-chart-y"><span>1,500</span><span>1,000</span><span>500</span><span>0</span></div>
            <div className="r3-trend-empty"><span>⌁</span><b>历史趋势数据尚未接入</b><small>当前 API 提供累计指标，不提供按时间分桶的趋势序列。</small></div>
          </div>
          <div className="r3-trend-foot"><span><b>{snapshot ? snapshot.metrics.open : '—'}</b> 运行中</span><span><b>{snapshot ? snapshot.metrics.done : '—'}</b> 已完成</span><span><b>{snapshot ? snapshot.metrics.agents : '—'}</b> Agent</span></div>
        </section>

        <section className="r3-home-panel r3-popular-panel">
          <PanelHeading title="热门 Agent" action="更多" to="/workspace/collaboration"/>
          {popularAgents.length ? <div className="r3-popular-list">{popularAgents.map((agent,index)=><div className="r3-popular-row" key={agent.name}><b className="r3-rank">{String(index+1).padStart(2,'0')}</b><span className={`r3-popular-icon ${['blue','violet','green'][index%3]}`}>{['▥','▤','‹›'][index%3]}</span><strong>{agent.title || agent.name}</strong><div className="r3-popular-meter"><i/></div><small>已启用</small></div>)}</div> : <div className="r3-module-empty">{loading ? '正在读取 Agent…' : '暂无已启用 Agent'}</div>}
          <p className="r3-data-note">当前按服务端 Agent 定义顺序展示；热门度排序 API 尚未提供。</p>
        </section>

        <section className="r3-home-panel r3-activity-panel">
          <PanelHeading title="最近动态" action="更多" to="/notifications"/>
          {activities.length ? <div className="r3-activity-list">{activities.map((item,index)=><div className="r3-activity-row" key={`${item.type}-${item.task}-${index}`}><i className={index%3===0?'blue':index%3===1?'violet':'green'}/><time>{formatTime(item.at)}</time><span>{item.text}</span></div>)}</div> : <div className="r3-module-empty">{loading ? '正在读取动态…' : '暂无最新动态'}</div>}
          <p className="r3-data-note">{error ?? '动态来自服务端 Office state API'}</p>
        </section>
      </div>
    </section>
  )
}

export function Round3OfficePage() {
  return <section className="r3-office-page"><div className="r3-office-page-heading"><div><span className="r3-home-eyebrow">DIGITAL WORKSPACE / 3D ENVIRONMENT</span><h1>3D 办公室</h1><p>探索智能体空间，查看楼层、区域与 Agent 实时状态。</p></div><Link to="/workspace/overview">← 返回工作台</Link></div></section>
}
