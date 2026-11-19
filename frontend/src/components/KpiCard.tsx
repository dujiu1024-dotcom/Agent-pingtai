import type { CSSProperties, ReactNode } from 'react'

export interface KpiCardProps {
  label: string
  value: string
  trend: string
  icon: ReactNode
  iconBackground: string
  trendClassName?: string
  style: CSSProperties
}

export function KpiCard({
  label,
  value,
  trend,
  icon,
  iconBackground,
  trendClassName = '',
  style,
}: KpiCardProps) {
  return (
    <article className="home-kpi-card absolute rounded-2xl bg-white/90 shadow-xl" style={style}>
      <span className="home-kpi-icon" style={{ background: iconBackground }} aria-hidden="true">
        {icon}
      </span>
      <span className="home-kpi-label">{label}</span>
      <div className="home-kpi-value">
        <strong>{value}</strong>
        <span className={`home-kpi-trend ${trendClassName}`}>{trend}</span>
      </div>
    </article>
  )
}
