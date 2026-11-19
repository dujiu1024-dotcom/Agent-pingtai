import type { CSSProperties } from 'react'

export interface PopularAgent {
  id: string
  name: string
  runs: string
  share: number
}

export interface PopularAgentsPanelProps {
  agents?: PopularAgent[]
  onMore?: () => void
  style?: CSSProperties
}

const panelStyle: CSSProperties = {
  left: 648,
  top: 752,
  width: 425,
  height: 230,
}

const rankColors = ['#4f7cff', '#a78bfa', '#34d399']

export function PopularAgentsPanel({ agents = [], onMore, style }: PopularAgentsPanelProps) {
  return (
    <section
      className="absolute box-border overflow-hidden rounded-2xl border border-white/95 bg-white/90 p-[22px] text-[#1c2540] shadow-[0_18px_36px_-12px_rgba(50,80,170,.34)] backdrop-blur"
      style={{ ...panelStyle, ...style }}
      aria-labelledby="popular-agents-panel-title"
    >
      <header className="flex items-center justify-between">
        <h2 id="popular-agents-panel-title" className="m-0 text-lg font-semibold">热门 Agent</h2>
        <button type="button" onClick={onMore} className="border-0 bg-transparent p-0 text-sm text-[#6b7794] hover:text-[#2f6af0]">更多 →</button>
      </header>
      {agents.length ? (
        <ol className="mt-3 space-y-3">
          {agents.slice(0, 3).map((agent, index) => (
            <li key={agent.id} className="flex h-8 items-center gap-2.5 text-sm">
              <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-[#ffe9cf] text-xs font-bold text-[#e07a10]">{index + 1}</span>
              <span className="w-[118px] shrink-0 truncate">{agent.name}</span>
              <span className="h-[5px] min-w-0 flex-1 overflow-hidden rounded-full bg-[#e4eafa]">
                <span className="block h-full rounded-full" style={{ width: `${Math.max(0, Math.min(100, agent.share))}%`, background: rankColors[index] }} />
              </span>
              <span className="w-[62px] shrink-0 text-right text-xs text-[#6b7794]">{agent.runs}</span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="mt-8 text-center text-sm text-[#6b7794]">暂无 Agent 统计</p>
      )}
    </section>
  )
}
