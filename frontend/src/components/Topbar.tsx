import { Link } from 'react-router-dom'

type TopbarProps = { title: string }

export function Topbar({ title }: TopbarProps) {
  return (
    <header className="r3-topbar">
      <div className="r3-breadcrumb">
        <Link to="/home">Agent 平台</Link>
        <span>/</span>
        <b>{title}</b>
      </div>
      <div className="r3-top-actions">
        <Link className="r3-search-trigger" to="/search" aria-label="打开全局搜索">
          <span>⌕</span>
          <span>搜索 Agent、任务、文档…</span>
          <kbd>Ctrl K</kbd>
        </Link>
        <Link className="r3-icon-action" to="/notifications" aria-label="通知中心">♧</Link>
        <span className="r3-user-avatar" aria-label="Agent Platform 用户">A</span>
      </div>
    </header>
  )
}
