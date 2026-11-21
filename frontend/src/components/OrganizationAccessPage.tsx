import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { ErrorState, LoadingState } from '@/components/States'
import { apiClient } from '@/lib/api/api-client'
import { displayValue, fieldLabel } from '@/lib/i18n/labels'
import './OrganizationAccessPage.css'

type OrganizationAccessPageProps = {
  page: 'organization' | 'rbac'
}

type AdminSection = {
  section: string
  rows: Array<Record<string, unknown>>
  count: number
}

const PAGE_SIZE = 10

/** 数据库主键/外键不直接展示，改为服务端返回的可读名称。 */
const HIDDEN_COLUMNS = new Set([
  'id',
  'tenant_id',
  'user_id',
  'role_id',
  'subject_id',
  'resource_id',
  'binding_id',
  'section',
])

function renderCell(key: string, value: unknown): string {
  return displayValue(key, value)
}

function SectionTable({ section }: { section: AdminSection }) {
  const [page, setPage] = useState(1)
  const columns = section.rows.length > 0
    ? Object.keys(section.rows[0]).filter((column) => !HIDDEN_COLUMNS.has(column))
    : []
  if (columns.length === 0) return <p className="detail-empty">没有记录。</p>
  const totalPages = Math.max(1, Math.ceil(section.rows.length / PAGE_SIZE))
  const currentPage = Math.min(page, totalPages)
  const start = (currentPage - 1) * PAGE_SIZE
  const visibleRows = section.rows.slice(start, start + PAGE_SIZE)
  return (
    <>
      <div className="generic-table-wrap">
        <table className="generic-table">
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column}>{fieldLabel(column)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((row, index) => (
              <tr key={start + index}>
                {columns.map((column) => (
                  <td key={column}>{renderCell(column, row[column])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {section.rows.length > PAGE_SIZE && (
        <nav className="admin-pagination" aria-label="分页">
          <span>
            第 {currentPage} / {totalPages} 页 · 共 {section.rows.length} 条
          </span>
          <div className="admin-pagination-actions">
            <button
              type="button"
              className="button-secondary"
              onClick={() => setPage(currentPage - 1)}
              disabled={currentPage <= 1}
            >
              上一页
            </button>
            <button
              type="button"
              className="button-secondary"
              onClick={() => setPage(currentPage + 1)}
              disabled={currentPage >= totalPages}
            >
              下一页
            </button>
          </div>
        </nav>
      )}
    </>
  )
}

export function OrganizationAccessPage({ page }: OrganizationAccessPageProps) {
  const organization = page === 'organization'
  const [activeTab, setActiveTab] = useState('')
  const apiPath = organization
    ? '/platform/v1/admin/organization'
    : '/platform/v1/admin/access-control'
  const query = useQuery({
    queryKey: ['admin', page],
    queryFn: () =>
      apiClient.get<{ items: AdminSection[] }>(apiPath),
    retry: false,
  })
  const sections = query.data?.items ?? []

  return (
    <section className="page-content">
      <div className="page-heading">
        <p className="eyebrow">管理</p>
        <h1>{organization ? '组织与成员' : '角色与权限'}</h1>
        <p>
          {organization
            ? '当前租户的组织、成员与成员关系，数据来自服务端 admin API。'
            : '当前租户的角色、绑定与权限键，数据来自服务端 admin API。'}
        </p>
      </div>
      {query.isPending && <LoadingState />}
      {query.isError && (
        <ErrorState
          description={
            query.error instanceof Error ? query.error.message : '加载失败'
          }
          onRetry={() => void query.refetch()}
        />
      )}
      {!query.isPending && !query.isError && sections.length === 0 && (
        <p className="detail-empty">当前租户暂无相关记录。</p>
      )}
      {(() => {
        const tabs = (organization
          ? [
              { id: 'tenants', label: '租户' },
              { id: 'users', label: '成员账号' },
            ]
          : [
              { id: 'roles', label: '角色' },
              { id: 'role_bindings', label: '角色绑定' },
              { id: 'role_permissions', label: '角色权限' },
            ]
        )
          .map((tab) => ({
            ...tab,
            data: sections.find((section) => section.section === tab.id),
          }))
          .filter((tab) => Boolean(tab.data))
        if (tabs.length === 0) return null
        const currentId = tabs.some((tab) => tab.id === activeTab)
          ? activeTab
          : tabs[0].id
        const current = tabs.find((tab) => tab.id === currentId)?.data
        return (
          <>
            <nav className="admin-tabs" role="tablist" aria-label="分区切换">
              {tabs.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  role="tab"
                  aria-selected={tab.id === currentId}
                  className={tab.id === currentId ? 'admin-tab active' : 'admin-tab'}
                  onClick={() => setActiveTab(tab.id)}
                >
                  {tab.label}
                  <span className="admin-tab-count">{tab.data?.count ?? 0}</span>
                </button>
              ))}
            </nav>
            {current && (
              <section className="admin-section">
                <SectionTable section={current} />
              </section>
            )}
          </>
        )
      })()}
    </section>
  )
}
