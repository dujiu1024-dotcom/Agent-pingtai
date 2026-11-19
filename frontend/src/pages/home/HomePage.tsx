import { ActivityFeedPanel } from '@/components/ActivityFeedPanel'
import { KpiCard } from '@/components/KpiCard'
import { PopularAgentsPanel } from '@/components/PopularAgentsPanel'
import { TaskListPanel } from '@/components/TaskListPanel'

const kpis = [
  {
    label: '今日运行',
    value: '1,248',
    trend: '↑ 12.5%',
    icon: '▶',
    iconBackground: 'linear-gradient(135deg, #4f8cff, #2f6af0)',
    style: { left: 667, top: 88, width: 186, height: 122 },
  },
  {
    label: '总成本',
    value: '¥ 368.20',
    trend: '↑ 8.7%',
    icon: '¥',
    iconBackground: 'linear-gradient(135deg, #ffb04a, #f07a1a)',
    style: { left: 863, top: 88, width: 194, height: 122 },
  },
  {
    label: '成功率',
    value: '98.5%',
    trend: '↑ 0.2%',
    icon: '✓',
    iconBackground: 'linear-gradient(135deg, #34d399, #10b981)',
    style: { left: 1068, top: 88, width: 200, height: 122 },
  },
  {
    label: '平均耗时',
    value: '12.8s',
    trend: '↓ 6.1%',
    icon: '◔',
    iconBackground: 'linear-gradient(135deg, #60a5fa, #2f6af0)',
    style: { left: 1280, top: 88, width: 203, height: 122 },
  },
] as const

export function HomePage() {
  return (
    <main className="home-page fixed inset-0 z-20 overflow-hidden" aria-label="工作台首页">
      <div className="home-canvas relative h-[1024px] w-[1536px]">
        {/* 欢迎区 */}
        <div className="home-welcome absolute" aria-hidden="true">
          <div className="home-welcome-mark">▲</div>
          <div>
            <h1>欢迎回来！</h1>
            <p>让 AI 赋能每一个工作场景</p>
          </div>
        </div>

        {/* 4 个 KPI 卡片 — 精确像素 */}
        {kpis.map((kpi) => (
          <KpiCard key={kpi.label} {...kpi} />
        ))}

        {/* 发光圆 — 精确像素 360×300 left=640 top=410 */}
        <div className="home-glow absolute rounded-full" aria-hidden="true" />

        {/* Home 下半 3 个面板 — 精确像素 */}
        <TaskListPanel />
        <PopularAgentsPanel />
        <ActivityFeedPanel />
      </div>
    </main>
  )
}
