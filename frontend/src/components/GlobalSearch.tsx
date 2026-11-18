import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { pageRoutes } from '@/routes/route-config'

export function GlobalSearch({ authEnabled, capabilities }: { authEnabled: boolean; capabilities: string[] }) {
  const [query, setQuery] = useState('')
  const matches = useMemo(() => {
    const value = query.trim().toLocaleLowerCase()
    if (!value) return []
    return pageRoutes.filter((route) => (!route.authOnly || authEnabled) && (!route.capability || capabilities.includes(route.capability))).filter((route) =>
      `${route.title} ${route.section} ${route.pageId}`.toLocaleLowerCase().includes(value),
    ).slice(0, 8)
  }, [query, authEnabled, capabilities])

  return (
    <section className="global-tool-page" aria-labelledby="global-search-title">
      <div className="page-heading">
        <p className="eyebrow">GLOBAL CAPABILITY</p>
        <h1 id="global-search-title">全局搜索</h1>
        <p>当前提供已注册页面的本地入口检索。跨实体数据搜索 API 尚未在 #753 契约中证实。</p>
      </div>
      <label className="search-field">
        <span>搜索页面与模块</span>
        <input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="输入页面名称、模块或 page_id" />
      </label>
      {query.trim() ? (
        matches.length ? (
          <ul className="search-results">
            {matches.map((route) => (
              <li key={route.pageId}>
                <Link to={route.path}>
                  <strong>{route.title}</strong>
                  <small>{route.section} · {route.pageId}</small>
                </Link>
              </li>
            ))}
          </ul>
        ) : <p className="muted">没有匹配的页面入口。</p>
      ) : <p className="muted">输入关键词检索导航页面。</p>}
    </section>
  )
}
