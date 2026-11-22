import { useCallback, useEffect, useState } from 'react'
import { ApiError, apiClient } from '@/lib/api'
import type { BootstrapConfig } from '@/lib/bootstrap/bootstrap'
type Row = Record<string, string | number | boolean | null>
type Section = { section: string; rows: Row[]; count: number }
type SettingsResponse = { items: Section[]; count: number }
const quotaFields = [['scope_type','范围类型'],['scope_id','范围标识'],['period_start','周期开始'],['period_end','周期结束'],['hard_tokens','Token 硬上限'],['hard_cost_usd','费用硬上限 USD'],['hard_concurrency','并发硬上限'],['soft_tokens','Token 软上限'],['soft_cost_usd','费用软上限 USD'],['priority','优先级']] as const
function valueOf(value: Row[string] | undefined) { return value === null || value === undefined || value === '' ? '—' : typeof value === 'boolean' ? (value ? '是' : '否') : String(value) }
function Table({ rows, fields }: { rows: Row[]; fields: readonly (readonly [string,string])[] }) {
  if (!rows.length) return <p className="settings-empty">暂无已配置记录</p>
  return <div className="settings-table-wrap"><table className="settings-table"><thead><tr>{fields.map(([key,label]) => <th key={key}>{label}</th>)}</tr></thead><tbody>{rows.map((row,index) => <tr key={String(row.scope_id ?? row.table_name ?? index)}>{fields.map(([key]) => <td key={key}>{valueOf(row[key])}</td>)}</tr>)}</tbody></table></div>
}
export function SystemSettingsPage({ config }: { config: BootstrapConfig }) {
  const [settings,setSettings] = useState<SettingsResponse | null>(null)
  const [loading,setLoading] = useState(true)
  const [error,setError] = useState<string | null>(null)
  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true); setError(null)
    try {
      const result = await apiClient.get<SettingsResponse>('/platform/v1/admin/settings',{etag:false,timeoutMs:10000,signal})
      if (!result || !Array.isArray(result.items)) throw new Error('设置 API 返回格式无效')
      setSettings(result)
    } catch (reason) {
      if (signal?.aborted) return
      if (reason instanceof ApiError && reason.status === 401) setError('登录状态已失效，请重新登录后查看租户设置。')
      else if (reason instanceof ApiError && reason.status === 403) setError('当前账号无权读取系统设置。')
      else setError(reason instanceof Error ? reason.message : '读取系统设置失败，请稍后重试。')
    } finally { if (!signal?.aborted) setLoading(false) }
  },[])
  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort() },[load])
  const quotas = settings?.items.find(item => item.section === 'quota_budgets')
  const counts = settings?.items.find(item => item.section === 'storage_counts')
  return <section className="page-content system-settings-page">
    <div className="page-heading settings-heading"><div><p className="eyebrow">SYS-SETTINGS · #812</p><h1>系统设置</h1><p>查看服务端启动配置及当前登录租户的持久化配置。此页面只读，不修改服务端环境变量或配额。</p></div><button className="settings-refresh" type="button" onClick={() => void load()} disabled={loading}>{loading ? '正在读取…' : '刷新配置'}</button></div>
    <section className="system-settings-section"><header><div><h2>运行模式</h2><p>认证模式和功能能力以服务端 bootstrap 为唯一依据。</p></div><span className={config.auth.enabled ? 'settings-status enabled' : 'settings-status'}>{config.auth.enabled ? '权限模式' : '免登录模式'}</span></header><div className="system-settings-grid"><div className="system-setting-value"><span>认证开关</span><strong>{config.auth.enabled ? '已启用' : '未启用'}</strong></div><div className="system-setting-value"><span>平台版本</span><strong>{config.version}</strong></div><div className="system-setting-value"><span>已声明能力</span><strong>{config.capabilities.length}</strong></div></div></section>
    <section className="system-settings-section"><header><div><h2>功能开关与能力</h2><p>只展示 bootstrap 明确声明的 capability，不提供前端本地开关。</p></div></header>{config.capabilities.length ? <ul className="settings-capability-list">{config.capabilities.map(item => <li key={item}><span>{item}</span><span className="settings-status enabled">服务端已声明</span></li>)}</ul> : <p className="settings-empty">服务端未声明功能能力。</p>}</section>
    <section className="system-settings-section"><header><div><h2>租户配额配置</h2><p>读取当前会话租户的已保存预算，不提供未经授权的写入操作。</p></div><span className="settings-status">{loading ? '读取中' : (quotas?.count ?? 0) + ' 条记录'}</span></header>{error ? <p className="settings-error" role="alert">{error}</p> : loading ? <p className="settings-empty" role="status">正在读取租户配置…</p> : <Table rows={quotas?.rows ?? []} fields={quotaFields} />}</section>
    <section className="system-settings-section"><header><div><h2>平台资源概况</h2><p>仅显示后端统计计数，不返回用户凭据、密钥或环境变量原文。</p></div><span className="settings-status">{loading ? '读取中' : (counts?.count ?? 0) + ' 项统计'}</span></header>{!loading && !error && <Table rows={counts?.rows ?? []} fields={ [['table_name','资源'],['row_count','记录数']] } />}</section>
    <p className="settings-readonly-note"><strong>配置安全边界</strong><span>认证模式和功能开关由部署端配置并通过 bootstrap 发布；环境变量、密钥及默认租户标识不向浏览器公开。配额和资源计数仅从受认证的管理 API 读取。本页面不提供配置写入。</span></p>
  </section>
}