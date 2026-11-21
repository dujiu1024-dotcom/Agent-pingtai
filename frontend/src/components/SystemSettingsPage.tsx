import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'

import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import { apiClient } from '@/lib/api'
import { valueLabel } from '@/lib/i18n/labels'
import { useAuth } from '@/providers/AuthProvider'
import './SystemSettingsPage.css'

type Section = { section: string; rows: Record<string, unknown>[]; count: number }
type SettingsResponse = { items?: Section[] }
type LocalePreference = {
  tenant_id: string
  language: string
  timezone: string
  region: string
  updated_by: string | null
}

const SCOPE_OPTIONS = [
  ['tenant', '租户'],
  ['project', '项目'],
  ['agent', 'Agent'],
  ['model', '模型'],
  ['run', '运行'],
  ['queue', '队列'],
  ['worker_pool', '工人池'],
] as const

const TABLE_LABELS: Record<string, string> = {
  platform_tenants: '租户',
  platform_users: '成员',
  platform_agents: 'Agent',
  platform_runs: '运行',
  platform_prompt_versions: 'Prompt 版本',
  platform_prompt_deployments: 'Prompt 部署',
}

const dateInput = (date: Date) => {
  const offset = date.getTimezoneOffset() * 60_000
  return new Date(date.getTime() - offset).toISOString().slice(0, 10)
}
const today = () => dateInput(new Date())
const plusDays = (days: number) => {
  const date = new Date()
  date.setDate(date.getDate() + days)
  return dateInput(date)
}
const isoStart = (value: string) => new Date(value + 'T00:00:00').toISOString()
const isoEnd = (value: string) => new Date(value + 'T23:59:59.999').toISOString()

const number = (value: unknown) =>
  typeof value === 'number' && Number.isFinite(value)
    ? new Intl.NumberFormat().format(value)
    : '—'
const decimal = (value: unknown) => {
  if (value === null || value === undefined || value === '') return '—'
  const parsed = Number(value)
  return Number.isFinite(parsed)
    ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 6 }).format(parsed)
    : '—'
}
const day = (value: unknown) => {
  if (typeof value !== 'string') return '—'
  return value.slice(0, 10)
}
const errorText = (error: unknown) => {
  const raw = error instanceof Error ? error.message : ''
  if (raw.includes('permission required')) return '当前账号没有配额管理权限。'
  if (raw.includes('not configured')) return '服务端未启用配额管理。'
  if (raw.includes('tenant budget scope')) return '租户范围只能配置当前租户。'
  if (raw.includes('must be')) return '配额值不合法，请填写非负数字。'
  return raw || '请求失败，请稍后重试。'
}

type SettingsTab = 'quota' | 'locale' | 'storage'

export function SystemSettingsPage() {
  const { session } = useAuth()
  const tenantId = session?.tenant_id ?? ''
  const [tab, setTab] = useState<SettingsTab>('quota')
  const [scopeType, setScopeType] = useState('tenant')
  const [scopeId, setScopeId] = useState(tenantId)
  const [periodStart, setPeriodStart] = useState(today)
  const [periodEnd, setPeriodEnd] = useState(() => plusDays(30))
  const [hardTokens, setHardTokens] = useState('')
  const [hardCost, setHardCost] = useState('')
  const [hardConcurrency, setHardConcurrency] = useState('')
  const [softTokens, setSoftTokens] = useState('')
  const [softCost, setSoftCost] = useState('')
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [language, setLanguage] = useState('zh-CN')
  const [timezone, setTimezone] = useState('Asia/Shanghai')
  const [region, setRegion] = useState('CN')
  const [localeSaving, setLocaleSaving] = useState(false)
  const [localeMessage, setLocaleMessage] = useState('')
  const [localeError, setLocaleError] = useState('')

  const settings = useQuery({
    queryKey: ['admin-settings'],
    queryFn: () => apiClient.get<SettingsResponse>('/platform/v1/admin/settings'),
    retry: false,
  })

  const locale = useQuery({
    queryKey: ['tenant-locale'],
    queryFn: () => apiClient.get<LocalePreference>('/platform/v1/tenant/locale'),
    retry: false,
  })

  useEffect(() => {
    if (!locale.data) return
    setLanguage(locale.data.language)
    setTimezone(locale.data.timezone)
    setRegion(locale.data.region)
  }, [locale.data])

  const budgetRows = settings.data?.items?.find((item) => item.section === 'quota_budgets')?.rows ?? []
  const storageRows = settings.data?.items?.find((item) => item.section === 'storage_counts')?.rows ?? []

  const integer = (value: string): number | null => {
    if (value.trim() === '') return null
    const parsed = Number(value)
    return Number.isInteger(parsed) && parsed >= 0 ? parsed : NaN
  }
  const amount = (value: string): string | null => {
    if (value.trim() === '') return null
    const parsed = Number(value)
    return Number.isFinite(parsed) && parsed >= 0 ? value.trim() : ''
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setMessage('')
    setError('')
    if (!scopeId.trim()) {
      setError('请填写适用范围。')
      return
    }
    if (!periodStart || !periodEnd || periodStart >= periodEnd) {
      setError('请填写有效的周期，开始日期需早于结束日期。')
      return
    }
    const payload = {
      scope_type: scopeType,
      scope_id: scopeId.trim(),
      period_start: isoStart(periodStart),
      period_end: isoEnd(periodEnd),
      hard_tokens: integer(hardTokens),
      hard_cost_usd: amount(hardCost),
      hard_concurrency: integer(hardConcurrency),
      soft_tokens: integer(softTokens),
      soft_cost_usd: amount(softCost),
    }
    const invalid = [
      ['硬性 Token 上限', payload.hard_tokens],
      ['并发上限', payload.hard_concurrency],
      ['软性 Token 上限', payload.soft_tokens],
    ].find(([, value]) => typeof value === 'number' && Number.isNaN(value))
    if (invalid) {
      setError(`${invalid[0]}需要填写非负整数。`)
      return
    }
    if (payload.hard_cost_usd === '' || payload.soft_cost_usd === '') {
      setError('费用上限需要填写非负数字。')
      return
    }
    setSaving(true)
    try {
      await apiClient.post('/platform/v1/quotas/budgets', payload)
      setMessage('配额已保存。')
      await settings.refetch()
    } catch (reason) {
      setError(errorText(reason))
    } finally {
      setSaving(false)
    }
  }

  const submitLocale = async (event: FormEvent) => {
    event.preventDefault()
    setLocaleMessage('')
    setLocaleError('')
    const languageValue = language.trim()
    const timezoneValue = timezone.trim()
    const regionValue = region.trim()
    if (!languageValue || !timezoneValue || !regionValue) {
      setLocaleError('语言、时区与地区均为必填项。')
      return
    }
    if (!/^[A-Z]{2}$/.test(regionValue)) {
      setLocaleError('地区需填写大写 ISO 3166-1 两位代码，例如 CN。')
      return
    }
    setLocaleSaving(true)
    try {
      await apiClient.put('/platform/v1/tenant/locale', {
        language: languageValue,
        timezone: timezoneValue,
        region: regionValue,
      })
      setLocaleMessage('语言与地区已保存。')
      await locale.refetch()
    } catch (reason) {
      const text = errorText(reason)
      setLocaleError(
        text.includes('permission') || text.includes('403')
          ? '当前账号没有租户管理权限，无法修改语言与地区。'
          : text,
      )
    } finally {
      setLocaleSaving(false)
    }
  }

  return (
    <section className="page-content settings-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">系统管理 / 系统设置</p>
          <h1>系统设置</h1>
          <p>配置配额与预算上限，并查看服务端存储统计。</p>
        </div>
        <button
          type="button"
          className="button-secondary"
          onClick={() => void settings.refetch()}
          disabled={settings.isFetching}
        >
          {settings.isFetching ? '刷新中…' : '刷新数据'}
        </button>
      </div>

      <nav className="panel-tabs" role="tablist" aria-label="系统设置分区">
        <button type="button" role="tab" aria-selected={tab === 'quota'} className={tab === 'quota' ? 'panel-tab active' : 'panel-tab'} onClick={() => setTab('quota')}>
          配额管理<span className="panel-tab-count">{budgetRows.length}</span>
        </button>
        <button type="button" role="tab" aria-selected={tab === 'locale'} className={tab === 'locale' ? 'panel-tab active' : 'panel-tab'} onClick={() => setTab('locale')}>
          语言与地区
        </button>
        <button type="button" role="tab" aria-selected={tab === 'storage'} className={tab === 'storage' ? 'panel-tab active' : 'panel-tab'} onClick={() => setTab('storage')}>
          存储统计<span className="panel-tab-count">{storageRows.length}</span>
        </button>
      </nav>

      {tab === 'quota' && (
        <div className="settings-grid">
          <section className="settings-card">
            <header>
              <h2>配额与预算</h2>
              <p>按范围设置 Token、费用和并发上限；留空表示不限制。</p>
          </header>
          <form className="settings-form" onSubmit={(event) => void submit(event)}>
            <div className="settings-row">
              <label>
                适用范围
                <select
                  value={scopeType}
                  onChange={(event) => {
                    setScopeType(event.target.value)
                    if (event.target.value === 'tenant') setScopeId(tenantId)
                  }}
                >
                  {SCOPE_OPTIONS.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                范围标识
                <input
                  value={scopeId}
                  onChange={(event) => setScopeId(event.target.value)}
                  readOnly={scopeType === 'tenant'}
                  placeholder={scopeType === 'tenant' ? tenantId || '当前租户' : '输入项目 / Agent / 模型等标识'}
                />
              </label>
            </div>
            <div className="settings-row">
              <label>
                周期开始
                <input type="date" value={periodStart} max={periodEnd || undefined} onChange={(event) => setPeriodStart(event.target.value)} />
              </label>
              <label>
                周期结束
                <input type="date" value={periodEnd} min={periodStart || undefined} onChange={(event) => setPeriodEnd(event.target.value)} />
              </label>
            </div>
            <div className="settings-row settings-row-three">
              <label>
                硬性 Token 上限
                <input type="number" min={0} step={1} value={hardTokens} onChange={(event) => setHardTokens(event.target.value)} placeholder="不限制" />
              </label>
              <label>
                硬性费用上限（美元）
                <input type="number" min={0} step="0.0001" value={hardCost} onChange={(event) => setHardCost(event.target.value)} placeholder="不限制" />
              </label>
              <label>
                并发上限
                <input type="number" min={0} step={1} value={hardConcurrency} onChange={(event) => setHardConcurrency(event.target.value)} placeholder="不限制" />
              </label>
            </div>
            <div className="settings-row">
              <label>
                软性 Token 上限
                <input type="number" min={0} step={1} value={softTokens} onChange={(event) => setSoftTokens(event.target.value)} placeholder="不限制" />
              </label>
              <label>
                软性费用上限（美元）
                <input type="number" min={0} step="0.0001" value={softCost} onChange={(event) => setSoftCost(event.target.value)} placeholder="不限制" />
              </label>
            </div>
            {error && <p className="settings-error" role="alert">{error}</p>}
            {message && <p className="settings-success" role="status">{message}</p>}
            <div className="settings-actions">
              <button className="button-primary" type="submit" disabled={saving}>
                {saving ? '保存中…' : '保存配额'}
              </button>
            </div>
          </form>
        </section>

        <section className="settings-card">
          <header>
            <h2>已生效配额</h2>
            <p>来自服务端配额表，仅展示当前租户。</p>
          </header>
          {settings.isPending ? (
            <LoadingState label="正在读取配额" />
          ) : settings.isError ? (
            <ErrorState title="配额读取失败" description={errorText(settings.error)} onRetry={() => void settings.refetch()} />
          ) : budgetRows.length === 0 ? (
            <EmptyState title="暂无配额配置" description="在左侧表单保存后，这里会显示当前租户的配额记录。" />
          ) : (
            <div className="settings-table-wrap">
              <table className="settings-table">
                <thead>
                  <tr>
                    <th>范围类型</th>
                    <th>范围</th>
                    <th>周期</th>
                    <th>硬性 Token</th>
                    <th>硬性费用（美元）</th>
                    <th>并发</th>
                    <th>软性 Token</th>
                    <th>软性费用（美元）</th>
                  </tr>
                </thead>
                <tbody>
                  {budgetRows.map((row, index) => (
                    <tr key={index}>
                      <td>{valueLabel(row.scope_type)}</td>
                      <td>{String(row.scope_id ?? '—')}</td>
                      <td>{day(row.period_start)} ~ {day(row.period_end)}</td>
                      <td>{number(row.hard_tokens)}</td>
                      <td>{decimal(row.hard_cost_usd)}</td>
                      <td>{number(row.hard_concurrency)}</td>
                      <td>{number(row.soft_tokens)}</td>
                      <td>{decimal(row.soft_cost_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
          </div>
      )}

      {tab === 'locale' && (
        <div className="settings-grid">
          <section className="settings-card">
            <header>
              <h2>语言与地区</h2>
            <p>按租户设置默认语言、时区和地区代码；保存后对新请求生效。</p>
          </header>
          <form className="settings-form" onSubmit={(event) => void submitLocale(event)}>
            <div className="settings-row">
              <label>
                语言
                <input
                  value={language}
                  onChange={(event) => setLanguage(event.target.value)}
                  placeholder="例如 zh-CN"
                />
              </label>
              <label>
                时区
                <input
                  value={timezone}
                  onChange={(event) => setTimezone(event.target.value)}
                  placeholder="例如 Asia/Shanghai"
                />
              </label>
            </div>
            <div className="settings-row">
              <label>
                地区代码
                <input
                  value={region}
                  onChange={(event) => setRegion(event.target.value.toUpperCase())}
                  placeholder="例如 CN"
                  maxLength={2}
                />
              </label>
            </div>
            {locale.data?.updated_by && (
              <p className="settings-hint">最近修改人：{locale.data.updated_by}</p>
            )}
            {localeError && <p className="settings-error" role="alert">{localeError}</p>}
            {localeMessage && <p className="settings-success" role="status">{localeMessage}</p>}
            <div className="settings-actions">
              <button className="button-primary" type="submit" disabled={localeSaving}>
                {localeSaving ? '保存中…' : '保存语言与地区'}
              </button>
            </div>
          </form>
        </section>
          </div>
      )}

      {tab === 'storage' && (
        <section className="settings-card">
          <header>
            <h2>存储统计</h2>
            <p>平台核心表记录数，用于容量与清理参考。</p>
        </header>
        {settings.isPending ? (
          <LoadingState label="正在读取存储统计" />
        ) : storageRows.length === 0 ? (
          <EmptyState title="暂无存储统计" description="服务端未返回记录数。" />
        ) : (
          <div className="settings-storage">
            {storageRows.map((row) => (
              <article key={String(row.table_name)}>
                <span>{TABLE_LABELS[String(row.table_name)] ?? String(row.table_name)}</span>
                <strong>{number(row.row_count)}</strong>
              </article>
            ))}
          </div>
        )}
      </section>
      )}
    </section>
  )
}
