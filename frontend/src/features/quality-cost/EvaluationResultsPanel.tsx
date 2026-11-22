import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError, apiClient } from '@/lib/api'
import { EmptyState, ErrorState, LoadingState } from '@/components/States'

type Envelope<T> = { items?: T[]; count?: number }
type Dataset = { dataset_id: string; version?: number; latest_version?: number; case_count?: number; versions?: number[]; created_at?: string }
type DatasetVersion = { dataset_id: string; version: number; case_count: number; created_by?: string; created_at?: string }
type EvaluationRun = {
  eval_run_id?: string
  run_id?: string
  dataset_id?: string
  dataset_version?: number
  model_key?: string
  gate_passed?: boolean
  pass_rate?: number
  average_score?: number
  average_recall_at_k?: number
  created_at?: string
  metrics?: Record<string, number | null>
  report?: Record<string, unknown> | string
  [key: string]: unknown
}
type Comparison = {
  report_id: string
  dataset_id: string
  dataset_version: number
  variant_count: number
  metric_names: string[]
  variants: Array<{ eval_run_id: string; model_key: string; gate_passed: boolean; metrics: Record<string, number | null> }>
  comparisons_to_recommended: Array<{ eval_run_id: string; model_key: string; delta_to_recommended: Record<string, number> }>
  recommended: { eval_run_id: string; model_key: string; reason: string }
}
const base = '/platform/v1/observability/evaluations'
const itemsOf = <T,>(value: Envelope<T> | T[] | undefined): T[] =>
  Array.isArray(value) ? value : value?.items ?? []
const idOf = (row: EvaluationRun) => String(row.eval_run_id ?? row.run_id ?? '')
const dateText = (value?: string) => value ? new Date(value).toLocaleString() : '—'
const reportOf = (row: EvaluationRun): Record<string, unknown> => {
  if (row.report && typeof row.report === 'object') return row.report
  if (typeof row.report === 'string') {
    try { const parsed: unknown = JSON.parse(row.report); return parsed && typeof parsed === 'object' ? parsed as Record<string, unknown> : {} } catch { return {} }
  }
  return {}
}
const runMetric = (row: EvaluationRun, key: string): number | null => {
  const report = reportOf(row)
  const metrics = report.metrics && typeof report.metrics === 'object' ? report.metrics as Record<string, unknown> : {}
  const value = row[key] ?? metrics[key] ?? report[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}
const metricLabel: Record<string, string> = {
  average_score: '平均评分',
  pass_rate: '通过率',
  average_recall_at_k: '平均 Recall@K',
  cost_usd: '费用（USD）',
  cost_per_run: '单次运行费用',
  latency_ms: '延迟（ms）',
  p50_latency_ms: 'P50 延迟（ms）',
  tokens: 'Token',
}
const displayMetric = (key: string, value: number | null | undefined) => {
  if (value == null || !Number.isFinite(value)) return '—'
  if (key === 'pass_rate' || key === 'average_recall_at_k') return (value * 100).toFixed(2) + '%'
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 6 }).format(value)
}
const errorText = (error: unknown) => {
  if (error instanceof ApiError) {
    if (error.status === 401) return '需要登录后访问评测数据。'
    if (error.status === 403) return '当前身份没有评测管理权限。'
    if (error.status === 404) return '数据集、运行或比较报告不存在。'
    if (error.status === 503) return '评测服务或比较报告存储未配置。'
  }
  return error instanceof Error ? error.message : '请求失败。'
}

export function EvaluationResultsPanel() {
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [datasetId, setDatasetId] = useState('')
  const [versions, setVersions] = useState<DatasetVersion[]>([])
  const [version, setVersion] = useState('')
  const [runs, setRuns] = useState<EvaluationRun[]>([])
  const [selectedRuns, setSelectedRuns] = useState<string[]>([])
  const [comparison, setComparison] = useState<Comparison | null>(null)
  const [comparisonId, setComparisonId] = useState('')
  const [loadingDatasets, setLoadingDatasets] = useState(true)
  const [loadingVersions, setLoadingVersions] = useState(false)
  const [loadingRuns, setLoadingRuns] = useState(false)
  const [comparing, setComparing] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const loadDatasets = useCallback(async () => {
    setLoadingDatasets(true)
    setError('')
    try {
      const response = await apiClient.get<Envelope<Dataset>>(`${base}/datasets?limit=100`)
      const rows = itemsOf(response)
      setDatasets(rows)
      if (!datasetId && rows[0]?.dataset_id) setDatasetId(rows[0].dataset_id)
    } catch (reason) {
      setError(errorText(reason))
    } finally {
      setLoadingDatasets(false)
    }
  }, [datasetId])

  const loadVersions = useCallback(async (id: string) => {
    if (!id.trim()) { setVersions([]); setRuns([]); return }
    setLoadingVersions(true)
    setError('')
    setComparison(null)
    setComparisonId('')
    setSelectedRuns([])
    try {
      const response = await apiClient.get<Envelope<DatasetVersion>>(`${base}/datasets/${encodeURIComponent(id.trim())}/versions`)
      const rows = itemsOf(response)
      setVersions(rows)
      const latest = rows[0]
      setVersion(String(latest?.version ?? ''))
    } catch (reason) {
      setVersions([])
      setError(errorText(reason))
    } finally {
      setLoadingVersions(false)
    }
  }, [])

  const loadRuns = useCallback(async (id: string, selectedVersion: string) => {
    if (!id.trim() || !selectedVersion) { setRuns([]); return }
    setLoadingRuns(true)
    setError('')
    setComparison(null)
    setComparisonId('')
    setSelectedRuns([])
    try {
      const response = await apiClient.get<Envelope<EvaluationRun>>(
        `${base}?dataset_id=${encodeURIComponent(id.trim())}&limit=100`,
      )
      const rows = itemsOf(response).filter((row) => row.dataset_version == null || row.dataset_version === Number(selectedVersion))
      setRuns(rows)
    } catch (reason) {
      setRuns([])
      setError(errorText(reason))
    } finally {
      setLoadingRuns(false)
    }
  }, [])

  useEffect(() => { void loadDatasets() }, [loadDatasets])
  useEffect(() => { if (datasetId) void loadVersions(datasetId) }, [datasetId, loadVersions])
  useEffect(() => { if (datasetId && version) void loadRuns(datasetId, version) }, [datasetId, version, loadRuns])

  const runIds = useMemo(() => runs.map(idOf).filter(Boolean), [runs])
  const toggleRun = (id: string) => setSelectedRuns((current) =>
    current.includes(id) ? current.filter((item) => item !== id) : current.length < 32 ? [...current, id] : current,
  )

  async function createComparison() {
    if (!datasetId || !version || selectedRuns.length < 2) return
    setComparing(true)
    setError('')
    setNotice('')
    try {
      const result = await apiClient.post<Comparison>(`${base}/comparisons`, {
        dataset_id: datasetId,
        dataset_version: Number(version),
        eval_run_ids: selectedRuns,
      })
      setComparison(result)
      setComparisonId(result.report_id)
      setNotice(`已生成比较报告：${result.report_id}`)
    } catch (reason) {
      setError(errorText(reason))
    } finally {
      setComparing(false)
    }
  }

  async function loadComparison() {
    if (!comparisonId.trim()) return
    setError('')
    setNotice('')
    try {
      const result = await apiClient.get<Comparison>(`${base}/comparisons/${encodeURIComponent(comparisonId.trim())}`)
      setComparison(result)
      setDatasetId(result.dataset_id)
      setVersion(String(result.dataset_version))
      setNotice('已加载保存的比较报告。')
    } catch (reason) {
      setComparison(null)
      setError(errorText(reason))
    }
  }

  const selectedDataset = datasets.find((row) => row.dataset_id === datasetId)

  return <div className="cq-eval-results">
    {error && <div className="cq-inline-error" role="alert">{error}</div>}
    {notice && <div className="cq-inline-success" role="status">{notice}</div>}
    <section className="cq-panel">
      <header><div><h2>评测数据集</h2><p>读取当前租户可访问的数据集目录与不可变版本；数据权限由服务端校验。</p></div><button className="button-secondary" type="button" onClick={() => void loadDatasets()} disabled={loadingDatasets}>刷新</button></header>
      {loadingDatasets ? <LoadingState label="正在读取数据集目录" /> : datasets.length ? <>
        <div className="cq-eval-controls">
          <label>数据集<select value={datasetId} onChange={(event) => setDatasetId(event.target.value)}><option value="">选择数据集</option>{datasets.map((row) => <option key={row.dataset_id} value={row.dataset_id}>{row.dataset_id}</option>)}</select></label>
          <label>版本<select value={version} onChange={(event) => setVersion(event.target.value)} disabled={!versions.length}><option value="">选择版本</option>{versions.map((row) => <option key={row.version} value={row.version}>v{row.version} · {row.case_count} 样本</option>)}</select></label>
          <span className="cq-eval-meta">{selectedDataset?.case_count == null ? '样本数以版本详情为准' : `目录样本数：${selectedDataset.case_count}`}</span>
        </div>
        {loadingVersions ? <LoadingState label="正在读取数据集版本" /> : versions.length ? <div className="cq-table-wrap"><table className="cq-table"><thead><tr><th>版本</th><th>样本数</th><th>创建者</th><th>创建时间</th></tr></thead><tbody>{versions.map((row) => <tr key={row.version}><td>v{row.version}</td><td>{row.case_count}</td><td>{row.created_by ?? '—'}</td><td>{dateText(row.created_at)}</td></tr>)}</tbody></table></div> : <EmptyState title="没有数据集版本" description="当前数据集尚无可读取的版本。" />}
      </> : <EmptyState title="暂无可访问的数据集" description="服务端没有返回数据集目录；可到评测数据集页面创建或导入数据集。" />}
      <p className="cq-footnote"><Link className="text-link" to="/operations/evaluation/datasets">打开数据集与 Case 管理</Link>。此处只呈现评测结果所需的目录和版本摘要，不复制数据集编辑能力。</p>
    </section>

    <section className="cq-panel">
      <header><div><h2>评测运行</h2><p>运行报告按数据集查询；只呈现所选数据集版本的服务端结果。</p></div><span>{runs.length} 条</span></header>
      {loadingRuns ? <LoadingState label="正在读取评测运行" /> : runs.length ? <>
        <div className="cq-table-wrap"><table className="cq-table"><thead><tr><th>选择</th><th>Run ID</th><th>模型</th><th>通过率</th><th>平均分</th><th>Recall@K</th><th>Gate</th><th>运行时间</th></tr></thead><tbody>{runs.map((row, index) => {
          const id = idOf(row)
          return <tr key={id || index}><td><input aria-label={`选择运行 ${id}`} type="checkbox" checked={selectedRuns.includes(id)} disabled={!id || (!selectedRuns.includes(id) && selectedRuns.length >= 32)} onChange={() => toggleRun(id)} /></td><td>{id ? <Link className="text-link" to={`/operations/evaluation/runs/${encodeURIComponent(id)}?dataset_id=${encodeURIComponent(datasetId)}`}>{id}</Link> : '—'}</td><td>{row.model_key ?? '—'}</td><td>{displayMetric('pass_rate', runMetric(row, 'pass_rate'))}</td><td>{displayMetric('average_score', runMetric(row, 'average_score'))}</td><td>{displayMetric('average_recall_at_k', runMetric(row, 'average_recall_at_k'))}</td><td>{row.gate_passed == null ? '—' : row.gate_passed ? '通过' : '未通过'}</td><td>{dateText(row.created_at)}</td></tr>
        })}</tbody></table></div>
        <div className="cq-eval-actions"><span>已选择 {selectedRuns.length} / 32（至少选择 2 个运行）</span><button className="button-primary" type="button" disabled={comparing || selectedRuns.length < 2} onClick={() => void createComparison()}>{comparing ? '生成中…' : '对比所选运行'}</button></div>
      </> : <EmptyState title="暂无评测运行" description="该数据集版本尚无可读取的评测报告。" />}
      <p className="cq-footnote">样本证据与单条运行详情由 <Link className="text-link" to="/operations/evaluation/runs">评测运行页面</Link> 提供；本页面不重复实现样本管理或评测执行。</p>
    </section>

    <section className="cq-panel">
      <header><div><h2>指标对比</h2><p>由服务端比较服务生成并持久化；仅显示双方都有的指标差值。</p></div></header>
      <div className="cq-eval-controls">
        <label>已保存报告 ID<input value={comparisonId} onChange={(event) => setComparisonId(event.target.value)} placeholder="输入 report_id 查询" /></label>
        <button className="button-secondary" type="button" disabled={!comparisonId.trim()} onClick={() => void loadComparison()}>加载报告</button>
      </div>
      {comparison ? <>
        <div className="cq-eval-recommend"><strong>推荐模型：{comparison.recommended.model_key}</strong><span>推荐运行：{comparison.recommended.eval_run_id}</span><small>{comparison.recommended.reason}</small></div>
        <div className="cq-table-wrap"><table className="cq-table"><thead><tr><th>模型 / Run</th><th>Gate</th>{comparison.metric_names.map((name) => <th key={name}>{metricLabel[name] ?? name}</th>)}</tr></thead><tbody>{comparison.variants.map((row) => <tr key={row.eval_run_id}><td><strong>{row.model_key}</strong><br /><small>{row.eval_run_id}</small></td><td>{row.gate_passed ? '通过' : '未通过'}</td>{comparison.metric_names.map((name) => <td key={name}>{displayMetric(name, row.metrics[name])}</td>)}</tr>)}</tbody></table></div>
        <div className="cq-table-wrap"><table className="cq-table"><thead><tr><th>运行</th><th>相对推荐项差值</th></tr></thead><tbody>{comparison.comparisons_to_recommended.map((row) => <tr key={row.eval_run_id}><td>{row.model_key} · {row.eval_run_id}</td><td>{Object.entries(row.delta_to_recommended).map(([key, value]) => `${metricLabel[key] ?? key}: ${value > 0 ? '+' : ''}${displayMetric(key, value)}`).join(' · ') || '无共同指标'}</td></tr>)}</tbody></table></div>
        <p className="cq-footnote">数据集：{comparison.dataset_id} · v{comparison.dataset_version} · {comparison.variant_count} 个运行 · 报告 ID：{comparison.report_id}</p>
        <a className="text-link" href={`${base}/comparisons/${encodeURIComponent(comparison.report_id)}/export?format=html`} target="_blank" rel="noreferrer">导出比较报告（HTML）</a>
      </> : <EmptyState title="尚无指标比较报告" description="在上方选择同一数据集版本的至少两个评测运行，生成可复查的指标矩阵。" />}
    </section>
  </div>
}
