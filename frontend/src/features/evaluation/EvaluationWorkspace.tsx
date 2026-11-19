import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import { ApiError, apiClient } from '@/lib/api'
import './evaluation-workspace.css'

type Envelope<T> = { items?: T[]; count?: number }
type DatasetVersion = { dataset_id: string; version: number; case_count: number; created_by?: string; created_at?: string }
type EvaluationCase = { case_id: string; input: unknown; expected_output?: unknown; rubric?: unknown; source_ref?: string | null }
type DatasetDetail = { dataset_id: string; version: number; case_count: number; cases: EvaluationCase[] }
type EvaluationReport = { eval_run_id?: string; run_id?: string; dataset_id?: string; dataset_version?: number; model_key?: string; pass_rate?: number; average_score?: number; average_recall_at_k?: number; gate_passed?: boolean; gate_failures?: string[]; [key: string]: unknown }
type RunResult = { eval_run_id: string; dataset_id: string; dataset_version: number; model_key: string; gate_passed: boolean; gate_failures: string[]; pass_rate: number; average_score: number; average_recall_at_k: number }
const base = '/platform/v1/observability/evaluations'
const readError = (error: unknown) => {
  if (error instanceof ApiError && error.status === 401) return '需要登录后访问评测数据。'
  if (error instanceof ApiError && error.status === 403) return '当前身份没有评测管理权限。'
  if (error instanceof ApiError && error.status === 404) return '数据集版本或评测记录不存在。'
  if (error instanceof ApiError && error.status === 503) return '评测服务当前未配置或不可用。'
  return error instanceof Error ? error.message : '请求失败。'
}
const itemsOf = <T,>(value: Envelope<T> | T[] | undefined): T[] => Array.isArray(value) ? value : value?.items ?? []
const pretty = (value: unknown) => JSON.stringify(value ?? {}, null, 2)
const dateText = (value?: string) => value ? new Date(value).toLocaleString() : '—'

export function EvaluationWorkspace() {
  const { datasetId, runId } = useParams()
  const [searchParams] = useSearchParams()
  const [activeTab, setActiveTab] = useState<'datasets' | 'runs'>(runId ? 'runs' : 'datasets')
  const [knownDataset, setKnownDataset] = useState(datasetId ?? searchParams.get('dataset_id') ?? '')
  const [versions, setVersions] = useState<DatasetVersion[]>([])
  const [detail, setDetail] = useState<DatasetDetail | null>(null)
  const [reports, setReports] = useState<EvaluationReport[]>([])
  const [selectedVersion, setSelectedVersion] = useState('1')
  const [modelKey, setModelKey] = useState('')
  const [importFormat, setImportFormat] = useState<'jsonl' | 'csv'>('jsonl')
  const [importContent, setImportContent] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [lastRun, setLastRun] = useState<RunResult | null>(null)

  const loadVersions = useCallback(async (id = knownDataset) => {
    if (!id.trim()) { setError('请输入数据集 ID。'); return }
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await apiClient.get<Envelope<DatasetVersion>>(`${base}/datasets/${encodeURIComponent(id.trim())}/versions`)
      const rows = itemsOf(result)
      setVersions(rows)
      if (rows.length) setSelectedVersion(String(rows[0].version))
      setKnownDataset(id.trim())
    } catch (reason) { setError(readError(reason)) } finally { setBusy(false) }
  }, [knownDataset])

  const loadCases = useCallback(async (id = knownDataset, version = selectedVersion) => {
    if (!id.trim() || !version) return
    setBusy(true); setError('')
    try {
      const result = await apiClient.get<DatasetDetail>(`${base}/datasets/${encodeURIComponent(id.trim())}/versions/${encodeURIComponent(version)}`)
      setDetail(result)
      setKnownDataset(id.trim())
    } catch (reason) { setError(readError(reason)); setDetail(null) } finally { setBusy(false) }
  }, [knownDataset, selectedVersion])

  const loadReports = useCallback(async (id = knownDataset) => {
    if (!id.trim()) { setError('请输入数据集 ID 后查询运行记录。'); return }
    setBusy(true); setError('')
    try {
      const result = await apiClient.get<Envelope<EvaluationReport>>(`${base}?dataset_id=${encodeURIComponent(id.trim())}&limit=100`)
      setReports(itemsOf(result))
      setKnownDataset(id.trim())
    } catch (reason) { setError(readError(reason)); setReports([]) } finally { setBusy(false) }
  }, [knownDataset])

  useEffect(() => {
    if (datasetId) { setKnownDataset(datasetId); void loadVersions(datasetId) }
  }, [datasetId]) // route change only; loadVersions is intentionally invoked for the route ID
  useEffect(() => {
    if (runId) setActiveTab('runs')
  }, [runId])

  const selectedReport = useMemo(() => reports.find(row => (row.eval_run_id ?? row.run_id) === runId), [reports, runId])

  async function importDataset() {
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await apiClient.post<{dataset_id:string;version:number;case_count:number;tenant_id:string}>(`${base}/datasets/import`, {
        dataset_id: knownDataset.trim() || undefined, format: importFormat, content: importContent,
      })
      setKnownDataset(result.dataset_id)
      setSelectedVersion(String(result.version))
      setNotice(`导入成功：${result.dataset_id} · v${result.version} · ${result.case_count} Cases`)
      setImportContent('')
      await loadVersions(result.dataset_id)
      await loadCases(result.dataset_id, String(result.version))
    } catch (reason) { setError(readError(reason)) } finally { setBusy(false) }
  }

  async function startRun() {
    if (!knownDataset.trim() || !modelKey.trim()) { setError('数据集 ID 和模型 Key 均为必填项。'); return }
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await apiClient.post<RunResult>(`${base}/run`, {
        dataset_id: knownDataset.trim(), dataset_version: Number(selectedVersion), model_key: modelKey.trim(),
      })
      setLastRun(result)
      setNotice(`评测已完成：${result.eval_run_id}`)
      await loadReports(knownDataset)
    } catch (reason) { setError(readError(reason)) } finally { setBusy(false) }
  }

  return <section className="page-content eval-page">
    <header className="eval-heading"><div><p className="eyebrow">P-UI-002-W1-5 · EVALUATION</p><h1>评测数据集与运行</h1><p>管理评测数据集版本、样本 Case，并执行模型评测和查看报告。</p></div><div className="eval-tabs"><button className={activeTab === 'datasets' ? 'active' : ''} onClick={() => setActiveTab('datasets')}>Datasets & Cases</button><button className={activeTab === 'runs' ? 'active' : ''} onClick={() => setActiveTab('runs')}>Evaluation Runs</button></div></header>
    {error && <div className="eval-alert" role="alert">{error}</div>}{notice && <div className="eval-notice" role="status">{notice}</div>}
    {activeTab === 'datasets' ? <div className="eval-grid">
      <section className="eval-panel"><h2>数据集版本</h2><p className="eval-help">后端提供按 dataset_id 查询版本；目前没有全局数据集目录接口，因此通过 ID 定位，不展示虚构的全量目录。</p><form className="eval-inline" onSubmit={event => {event.preventDefault(); void loadVersions()}}><input aria-label="数据集 ID" value={knownDataset} onChange={event => setKnownDataset(event.target.value)} placeholder="输入 dataset_id" /><button className="button-primary" disabled={busy}>查询版本</button></form>
      {versions.length ? <div className="eval-list">{versions.map(row => <button key={row.version} className={selectedVersion === String(row.version) ? 'eval-list-row selected' : 'eval-list-row'} onClick={() => {setSelectedVersion(String(row.version)); void loadCases(row.dataset_id, String(row.version))}}><strong>{row.dataset_id} · v{row.version}</strong><span>{row.case_count} Cases · {dateText(row.created_at)}</span></button>)}</div> : <p className="eval-empty">输入数据集 ID 查询已存在版本。</p>}
      {detail && <><h3>v{detail.version} · Cases ({detail.case_count})</h3><div className="eval-cases">{detail.cases.map(item => <details key={item.case_id}><summary>{item.case_id}</summary><pre>{pretty({input:item.input,expected_output:item.expected_output,rubric:item.rubric,source_ref:item.source_ref})}</pre></details>)}</div></>}
      </section>
      <section className="eval-panel"><h2>导入数据集</h2><p className="eval-help">支持后端 parser 声明的 CSV / JSONL 格式。每条记录包含 case 输入及可选期望输出、评分 rubric。</p><label>数据集 ID（留空由服务端生成）<input value={knownDataset} onChange={event => setKnownDataset(event.target.value)} placeholder="可选 dataset_id" /></label><label>格式<select value={importFormat} onChange={event => setImportFormat(event.target.value as 'jsonl'|'csv')}><option value="jsonl">JSONL</option><option value="csv">CSV</option></select></label><label>数据内容<textarea rows={10} value={importContent} onChange={event => setImportContent(event.target.value)} placeholder={'{"case_id":"case-1","input":"...","expected_output":"..."}'} /></label><button className="button-primary" disabled={busy || !importContent.trim()} onClick={() => void importDataset()}>导入并创建版本</button></section>
    </div> : <div className="eval-grid">
      <section className="eval-panel"><h2>运行记录</h2><p className="eval-help">报告列表按数据集查询；当前 API 未提供跨数据集的全局 Run 列表。</p><form className="eval-inline" onSubmit={event => {event.preventDefault(); void loadReports()}}><input aria-label="数据集 ID" value={knownDataset} onChange={event => setKnownDataset(event.target.value)} placeholder="输入 dataset_id" /><button className="button-primary" disabled={busy}>查询运行</button></form>
      {reports.length ? <div className="eval-list">{reports.map((row,i) => {const id = row.eval_run_id ?? row.run_id ?? `report-${i}`;return <Link key={id} className="eval-list-row" to={`/admin/evaluation/runs/${encodeURIComponent(String(id))}?dataset_id=${encodeURIComponent(knownDataset)}`}><strong>{String(id)}</strong><span>{String(row.model_key ?? '模型未记录')} · Pass rate {row.pass_rate == null ? '—' : `${(Number(row.pass_rate)*100).toFixed(1)}%`}</span></Link>})}</div> : <p className="eval-empty">查询指定数据集以查看已有评测报告。</p>}
      </section>
      <section className="eval-panel"><h2>发起评测</h2><p className="eval-help">调用已注册的 POST /evaluations/run；模型由服务端模型 Key 解析。</p><label>数据集 ID<input value={knownDataset} onChange={event => setKnownDataset(event.target.value)} placeholder="dataset_id" /></label><label>数据集版本<select value={selectedVersion} onChange={event => setSelectedVersion(event.target.value)}>{versions.map(row => <option key={row.version} value={row.version}>v{row.version} · {row.case_count} Cases</option>)}{!versions.length && <option value="1">v1</option>}</select></label><label>模型 Key<input value={modelKey} onChange={event => setModelKey(event.target.value)} placeholder="provider/model-key" /></label><button className="button-primary" disabled={busy || !knownDataset.trim() || !modelKey.trim()} onClick={() => void startRun()}>执行评测</button>{lastRun && <div className="eval-result"><strong>{lastRun.eval_run_id}</strong><span>Pass rate {(lastRun.pass_rate*100).toFixed(1)}% · 平均分 {lastRun.average_score}</span><span>Gate {lastRun.gate_passed ? '通过' : '未通过'}</span></div>}</section>
      {runId && <section className="eval-panel eval-run-detail"><h2>运行详情 · {runId}</h2><p className="eval-help">当前后端仅提供按 dataset_id 查询报告列表，详情由对应列表记录定位；请先在左侧加载该数据集报告。</p>{selectedReport ? <pre>{pretty(selectedReport)}</pre> : <p className="eval-empty">尚未加载包含此 Run 的数据集报告。请返回运行列表输入对应 dataset_id。</p>}</section>}
    </div>}
    <p className="eval-footnote">权限由服务端评测管理授权执行。页面不缓存或展示其他租户的数据；未提供的全局目录和 Run 单项 API 不会由前端模拟。</p>
  </section>
}
