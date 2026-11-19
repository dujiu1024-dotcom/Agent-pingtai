import type { CSSProperties } from 'react'

export interface ActivityFeedItem {
  id: string
  time: string
  description: string
  color?: string
}

export interface ActivityFeedPanelProps {
  activities?: ActivityFeedItem[]
  onMore?: () => void
  style?: CSSProperties
}

const panelStyle: CSSProperties = {
  left: 1091,
  top: 752,
  width: 420,
  height: 230,
}

export function ActivityFeedPanel({ activities = [], onMore, style }: ActivityFeedPanelProps) {
  return (
    <section
      className="absolute box-border overflow-hidden rounded-2xl border border-white/95 bg-white/90 p-[22px] text-[#1c2540] shadow-[0_18px_36px_-12px_rgba(50,80,170,.34)] backdrop-blur"
      style={{ ...panelStyle, ...style }}
      aria-labelledby="activity-feed-panel-title"
    >
      <header className="flex items-center justify-between">
        <h2 id="activity-feed-panel-title" className="m-0 text-lg font-semibold">最近动态</h2>
        <button type="button" onClick={onMore} className="border-0 bg-transparent p-0 text-sm text-[#6b7794] hover:text-[#2f6af0]">更多 →</button>
      </header>
      {activities.length ? (
        <ol className="mt-4 space-y-4">
          {activities.slice(0, 3).map((activity) => (
            <li key={activity.id} className="flex min-w-0 items-center gap-3 text-sm">
              <span className="h-[9px] w-[9px] shrink-0 rounded-full" style={{ background: activity.color ?? '#2f6af0' }} />
              <time className="shrink-0 text-[#6b7794]">{activity.time}</time>
              <span className="truncate">{activity.description}</span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="mt-8 text-center text-sm text-[#6b7794]">暂无动态</p>
      )}
    </section>
  )
}
