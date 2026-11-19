import type { CSSProperties } from 'react'

export interface TaskListItem {
  id: string
  title: string
  status: string
  detail?: string
  progress?: number
}

export interface TaskListPanelProps {
  tasks?: TaskListItem[]
  style?: CSSProperties
}

const panelStyle: CSSProperties = {
  left: 199,
  top: 752,
  width: 432,
  height: 230,
}

export function TaskListPanel({ tasks = [], style }: TaskListPanelProps) {
  return (
    <section
      className="absolute box-border overflow-hidden rounded-2xl border border-white/95 bg-white/90 p-[22px] text-[#1c2540] shadow-[0_18px_36px_-12px_rgba(50,80,170,.34)] backdrop-blur"
      style={{ ...panelStyle, ...style }}
      aria-labelledby="task-list-panel-title"
    >
      <header className="flex items-center justify-between">
        <h2 id="task-list-panel-title" className="m-0 text-lg font-semibold">任务列表</h2>
        <span className="rounded-lg bg-[#dcfce7] px-2.5 py-0.5 text-xs font-medium text-[#15803d]">● 运行正常</span>
      </header>
      {tasks.length ? (
        <ul className="mt-4 space-y-3">
          {tasks.slice(0, 3).map((task) => (
            <li key={task.id} className="min-w-0">
              <div className="flex items-center justify-between gap-3">
                <span className="truncate text-sm font-medium">{task.title}</span>
                <span className="shrink-0 text-xs text-[#6b7794]">{task.status}</span>
              </div>
              {typeof task.progress === 'number' && (
                <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-[#e4eafa]">
                  <div className="h-full rounded-full bg-[#2f6af0]" style={{ width: `${Math.max(0, Math.min(100, task.progress))}%` }} />
                </div>
              )}
              {task.detail && <p className="mb-0 mt-1 text-xs text-[#6b7794]">{task.detail}</p>}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-8 text-center text-sm text-[#6b7794]">暂无任务</p>
      )}
    </section>
  )
}
