import { Link } from 'react-router-dom'
import { OfficeWorkspace } from '@/components/OfficeWorkspace'
import './round3-workspace.css'

const quickLinks = [
  { icon: '✳', title: 'Agent 协作', detail: '查看智能体团队与协作运行', to: '/workspace/collaboration', tone: 'blue' },
  { icon: '⌘', title: 'Workflow', detail: '编排自动化工作流', to: '/admin/workflows', tone: 'violet' },
  { icon: '◈', title: '成本分摊', detail: '追踪模型用量与成本', to: '/workspace/quality-cost', tone: 'orange' },
  { icon: '▤', title: '任务中心', detail: '管理任务队列与执行记录', to: '/workspace/tasks', tone: 'green' },
]

const agents = [
  { name: '代码助手', role: '研发工程', color: 'blue', initials: '码' },
  { name: '市场分析助手', role: '市场研究', color: 'violet', initials: '市' },
  { name: '文档总结专家', role: '知识处理', color: 'orange', initials: '文' },
  { name: '数据分析专家', role: '数据洞察', color: 'green', initials: '数' },
]

function SectionTitle({ title, hint, to }: { title: string; hint?: string; to?: string }) {
  return <div className="r3-section-title"><div><h2>{title}</h2>{hint && <span>{hint}</span>}</div>{to && <Link to={to}>查看全部 <b>→</b></Link>}</div>
}

function Blank({ title, detail }: { title: string; detail: string }) {
  return <div className="r3-blank"><span className="r3-blank-icon">⌁</span><strong>{title}</strong><p>{detail}</p></div>
}

export function Round3Home() {
  return (
    <section className="r3-home page-content">
      <div className="r3-welcome">
        <div><div className="r3-kicker"><span /> AGENT PLATFORM · WORKSPACE</div><h1>早上好，欢迎回来</h1><p>这里是你的智能体工作台，集中掌握任务进展与团队动态。</p></div>
        <div className="r3-welcome-actions"><Link to="/workspace/tasks" className="r3-primary-button">＋ 创建任务</Link><Link to="/workspace/office" className="r3-quiet-button">进入数字办公室 ↗</Link></div>
      </div>

      <div className="r3-kpis">
        {[
          { label: '累计任务', value: '—', unit: '次', icon: '▤', tone: 'blue', note: '等待任务统计 API' },
          { label: '任务成功率', value: '—', unit: '%', icon: '✓', tone: 'green', note: '等待任务统计 API' },
          { label: '平均耗时', value: '—', unit: '分钟', icon: '◷', tone: 'violet', note: '等待运行时长聚合' },
          { label: '累计成本', value: '—', unit: 'USD', icon: '◈', tone: 'orange', note: '等待成本汇总 API' },
        ].map((item) => <article className="r3-kpi" key={item.label}><div className="r3-kpi-top"><span>{item.label}</span><i className={item.tone}>{item.icon}</i></div><div className="r3-kpi-value">{item.value}<small>{item.unit}</small></div><p>{item.note}</p></article>)}
      </div>

      <div className="r3-main-grid">
        <section className="r3-panel r3-trend-panel"><SectionTitle title="任务趋势" hint="近 7 天 · 任务量与成功率" /><div className="r3-chart-legend"><span><i className="blue-dot" />任务数</span><span><i className="green-dot" />成功率</span></div><div className="r3-chart"><div className="r3-chart-y"><span>100%</span><span>75%</span><span>50%</span><span>25%</span><span>0</span></div><div className="r3-chart-area"><div className="r3-chart-grid">{[0,1,2,3,4].map(x=><i key={x}/>)}</div><Blank title="暂无趋势数据" detail="接入任务统计接口后，将在此展示任务量与成功率变化。" /></div></div><div className="r3-trend-foot"><span><b className="blue-text">—</b> 今日运行</span><span><b className="green-text">—</b> 已完成</span><span><b>—</b> 进行中</span></div></section>

        <section className="r3-panel r3-task-panel"><SectionTitle title="任务列表" hint="实时跟踪任务状态" to="/workspace/tasks" /><div className="r3-tabs"><span className="active">全部任务</span><span>运行中</span><span>等待审批</span></div><Blank title="暂无任务记录" detail="任务创建后会显示在这里，包含执行进度、负责人和最近更新时间。" /></section>

        <section className="r3-panel"><SectionTitle title="热门 Agent" hint="常用智能体快捷入口" to="/workspace/collaboration" /><div className="r3-agent-list">{agents.map((agent) => <Link to="/workspace/collaboration" className="r3-agent" key={agent.name}><span className={'r3-avatar '+agent.color}>{agent.initials}</span><span className="r3-agent-copy"><strong>{agent.name}</strong><small>{agent.role}</small></span><span className="r3-agent-arrow">›</span></Link>)}</div></section>

        <section className="r3-panel"><SectionTitle title="最近动态" hint="工作空间最新动态" to="/notifications" /><Blank title="暂无最新动态" detail="Agent 更新、Workflow 变更、任务完成与审批事件将在此汇总。" /></section>
      </div>

      <section className="r3-panel r3-entry-panel"><SectionTitle title="快捷工作台" hint="快速进入常用功能" /><div className="r3-shortcuts">{quickLinks.map((item) => <Link to={item.to} className="r3-shortcut" key={item.title}><i className={item.tone}>{item.icon}</i><span><strong>{item.title}</strong><small>{item.detail}</small></span><b>→</b></Link>)}</div></section>
      <footer className="r3-footer"><span>Agent Platform</span><span>数据以服务端实时返回为准</span></footer>
    </section>
  )
}

export function Round3Office() {
  return <section className="r3-office-page page-content"><div className="r3-office-heading"><div><div className="r3-kicker"><span/> DIGITAL WORKSPACE</div><h1>3D 数字办公室</h1><p>探索智能体工作空间，查看楼层、区域与 Agent 状态。</p></div><div className="r3-office-live"><i/> 实时状态待连接</div></div><OfficeWorkspace /></section>
}
