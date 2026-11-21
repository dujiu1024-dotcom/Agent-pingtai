import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery } from '@tanstack/react-query'

import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import { ApiError, apiClient } from '@/lib/api'
import { fieldLabel, formatDateTime, valueLabel } from '@/lib/i18n/labels'
import './evaluation-insights.css'

type TabId = 'compare' | 'rollout' | 'scores'

interface EvalCaseResult {
  case_id: string
  passed?: boolean
  score?: number
  missing_terms?: string[]
  forbidden_terms_found?: string[]
  recall_at_k?: number | null
  reciprocal_rank?: number | null
}

interface EvalReportPayload {
  dataset_id?: string
  dataset_version?: number
  pass_rate?: number
  average_score?: number
  average_recall_at_k?: number | null
  results?: EvalCaseResult[]
  gate_failures?: string[]
  metrics?: Record<string, number>
}

interface EvalRun {
  eval_run_id: string
  dataset_id?: string
  dataset_version?: number
  model_key?: string
  report?: EvalReportPayload | string | null
  gate_passed?: boolean
  created_at?: string
}

interface MetricDelta {
  metric: string
  baseline: number
  candidate: number
  delta: number
  regressed: boolean
}

interface ComparisonResponse {
  comparison_id: string
  baseline_run_id: string
  candidate_run_id: string
  regressions: string[]
  deltas: MetricDelta[]
}

interface RolloutResult {
  experiment_id: string
  prompt_id: string
  selected_variant: string
  mode: string
  weights: Record<string, number>
  policy_revision: number
}

interface HumanScore {
  score_id: string
  eval_run_id: string
  case_id: string
  reviewer_id: string
  score: number
  rationale?: string
  created_at?: string
  updated_at?: string
}

interface ListResponse<T> {
  items?: T[]
}

const METRIC_LABELS: Record<string, string> = {
  pass_rate: '通过率',
  average_score: '平均分',
  average_recall_at_k: '平均召回率',
  retrieval_recall_at_k: '检索召回率',
  retrieval_precision_at_k: '检索精确率',
  retrieval_mrr: '检索 MRR',
  retrieval_ndcg_at_k: '检索 NDCG',
  answer_term_f1: '答案术语 F1',
  citation_precision: '引用精确率',
  citation_recall: '引用召回率',
  citation_validity: '引用有效性',
  latency_ms: '延迟（毫秒）',
  average_latency_ms: '平均延迟（毫秒）',
  p50_latency_ms: 'P50 延迟（毫秒）',
  p95_latency_ms: 'P95 延迟（毫秒）',
  cost_usd: '成本（美元）',
  cost_per_run: '单次运行成本（美元）',
  error_rate: '错误率',
  forbidden_term_rate: '禁用词命中率',
}

const RATIO_METRIC = /(rate|score|recall|precision|_f1|validity|mrr|ndcg)/

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const parseReport = (value: EvalRun['report']): EvalReportPayload | null => {
  if (!value) return null
  if (typeof value === 'string') {
    try {
      const parsed: unknown = JSON.parse(value)
      return isRecord(parsed) ? (parsed as EvalReportPayload) : null
    } catch {
      return null
    }
  }
  return isRecord(value) ? (value as EvalReportPayload) : null
}

const metricLabel = (key: string) => METRIC_LABELS[key] ?? fieldLabel(key)

const percent = (value: unknown): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return `${(value * 100).toFixed(1)}%`
}

const fixed = (value: unknown): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 3 }).format(value)
}

const metricValue = (key: string, value: unknown): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return RATIO_METRIC.test(key) && Math.abs(value) <= 1 ? percent(value) : fixed(value)
}

const describeError = (error: unknown): string => {
  if (error instanceof ApiError) {
    const detail = error.message ? `服务端信息：${error.message}` : ''
    switch (error.status) {
      case 401:
        return '登录状态已失效（401），请重新登录后再试。'
      case 403:
        return '当前账号缺少该操作所需的权限（403），请联系租户管理员。'
      case 404:
        return `当前部署未启用该能力，或目标记录不存在（404）。${detail}`
      case 409:
        return `当前状态与请求冲突（409），请核对评测运行或实验报告状态。${detail}`
      case 422:
        return `请求参数未通过服务端校验（422），请检查填写内容。${detail}`
      case 503:
        return `服务端尚未配置该能力（503）。${detail}`
      default:
        return detail || '请求失败，请稍后重试。'
    }
  }
  return error instanceof Error ? error.message : '请求失败，请稍后重试。'
}

const runTitle = (run: EvalRun) => {
  const id = run.eval_run_id.slice(0, 8)
  const model = run.model_key || '未知模型'
  const dataset = run.dataset_id
    ? `${run.dataset_id} v${run.dataset_version ?? '—'}`
    : '未知数据集'
  return `${id} · ${model} · ${dataset}`
}

function RunSelect({
  label,
  value,
  runs,
  onChange,
  exclude,
}: {
  label: string
  value: string
  runs: EvalRun[]
  onChange: (value: string) => void
  exclude?: string
}) {
  return (
    <label className="ei-field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">请选择评测运行</option>
        {runs
          .filter((run) => run.eval_run_id !== exclude)
          .map((run) => (
            <option value={run.eval_run_id} key={run.eval_run_id}>
              {runTitle(run)}
            </option>
          ))}
      </select>
    </label>
  )
}

function MetricTile({ label, value }: { label: string; value: string }) {
  return (
    <article className="ei-metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  )
}

export function EvaluationInsightsPage() {
  const [tab, setTab] = useState<TabId>('compare')
  const [baselineId, setBaselineId] = useState('')
  const [candidateId, setCandidateId] = useState('')
  const [experimentId, setExperimentId] = useState('')
  const [mode, setMode] = useState<'automatic' | 'manual'>('automatic')
  const [variant, setVariant] = useState('')
  const [rolloutPercentage, setRolloutPercentage] = useState('10')
  const [minimumScoreGain, setMinimumScoreGain] = useState('0')
  const [scoreRunId, setScoreRunId] = useState('')
  const [formError, setFormError] = useState('')

  const runsQuery = useQuery({
    queryKey: ['evaluation-insights', 'runs'],
    queryFn: () =>
      apiClient.get<ListResponse<EvalRun>>(
        '/platform/v1/observability/evaluations?limit=100',
      ),
    retry: false,
  })

  const comparison = useMutation({
    mutationFn: () =>
      apiClient.post<ComparisonResponse>(
        `/platform/v1/observability/evaluations/${encodeURIComponent(candidateId)}/compare`,
        { baseline_run_id: baselineId },
      ),
  })

  const rollout = useMutation({
    mutationFn: () => {
      const experiment = experimentId.trim()
      if (!experiment) throw new Error('实验 ID 为必填项')
      const percentage = Number(rolloutPercentage)
      const gain = Number(minimumScoreGain)
      if (!Number.isFinite(percentage) || percentage < 1 || percentage > 100) {
        throw new Error('灰度比例必须是 1 到 100 之间的数字')
      }
      if (!Number.isFinite(gain) || gain < 0) {
        throw new Error('最小分数增益必须是非负数字')
      }
      const body: Record<string, unknown> = {
        mode,
        rollout_percentage: percentage,
        minimum_score_gain: gain,
      }
      if (mode === 'manual') {
        const selected = variant.trim()
        if (!selected) throw new Error('手动模式下必须填写实验报告中存在的变体名称')
        body.variant = selected
      }
      return apiClient.post<RolloutResult>(
        `/platform/v1/evaluations/${encodeURIComponent(experiment)}/rollout`,
        body,
      )
    },
    onSuccess: () => setFormError(''),
  })

  const scores = useQuery({
    queryKey: ['evaluation-insights', 'scores', scoreRunId],
    queryFn: () =>
      apiClient.get<ListResponse<HumanScore>>(
        `/platform/v1/observability/evaluations/${encodeURIComponent(scoreRunId)}/scores`,
      ),
    enabled: Boolean(scoreRunId),
    retry: false,
  })

  const runs = useMemo(
    () => (Array.isArray(runsQuery.data?.items) ? runsQuery.data.items : []),
    [runsQuery.data],
  )
  const baseline = runs.find((run) => run.eval_run_id === baselineId)
  const candidate = runs.find((run) => run.eval_run_id === candidateId)
  const scoreRun = runs.find((run) => run.eval_run_id === scoreRunId)

  const baselineReport = parseReport(baseline?.report)
  const candidateReport = parseReport(candidate?.report)
  const scoreReport = parseReport(scoreRun?.report)

  const caseRows = useMemo(() => {
    const ids = new Set<string>()
    for (const item of baselineReport?.results ?? []) ids.add(item.case_id)
    for (const item of candidateReport?.results ?? []) ids.add(item.case_id)
    return [...ids].map((caseId) => ({
      caseId,
      baselineCase: baselineReport?.results?.find((item) => item.case_id === caseId),
      candidateCase: candidateReport?.results?.find((item) => item.case_id === caseId),
    }))
  }, [baselineReport, candidateReport])

  const canCompare = Boolean(baselineId && candidateId && baselineId !== candidateId)
  const scoreItems = Array.isArray(scores.data?.items) ? scores.data.items : []

  const submitRollout = () => {
    setFormError('')
    if (!experimentId.trim()) {
      setFormError('实验 ID 为必填项')
      return
    }
    rollout.mutate()
  }

  const renderRunsState = () => {
    if (runsQuery.isPending) return <LoadingState label="正在读取评测运行" />
    if (runsQuery.isError) {
      return (
        <ErrorState
          title="评测运行暂不可用"
          description={describeError(runsQuery.error)}
          onRetry={() => void runsQuery.refetch()}
        />
      )
    }
    if (runs.length === 0) {
      return (
        <EmptyState
          title="暂无评测运行"
          description="当前租户还没有可读取的评测运行记录；本页不会生成虚构的运行或分数。"
        />
      )
    }
    return null
  }

  return (
    <section className="page-content ei-page">
      <div className="page-heading ei-heading">
        <div>
          <p className="eyebrow">智能体运营 / 评测洞察</p>
          <h1>评测洞察</h1>
          <p>基于服务端评测运行报告进行对比、灰度发布与评分查看。</p>
        </div>
        <button
          className="button-secondary"
          type="button"
          onClick={() => void runsQuery.refetch()}
          disabled={runsQuery.isFetching}
        >
          刷新运行
        </button>
      </div>

      <nav className="panel-tabs" role="tablist" aria-label="评测洞察分区">
        {(
          [
            ['compare', '运行对比', runs.length],
            ['rollout', '灰度发布', 0],
            ['scores', '评分与用例', scoreRun ? 1 : 0],
          ] as const
        ).map(([id, label, count]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            className={tab === id ? 'panel-tab active' : 'panel-tab'}
            onClick={() => setTab(id)}
          >
            {label}
            {count > 0 && <span className="panel-tab-count">{count}</span>}
          </button>
        ))}
      </nav>

      {renderRunsState()}

      {tab === 'compare' && runs.length > 0 && (
        <>
          <section className="ei-panel">
            <header>
              <div>
                <h2>选择两次运行</h2>
                <p>
                  对比基于两次真实运行的报告；服务端对比接口不可用时，下方并排展示原始指标与用例结果。
                </p>
              </div>
            </header>
            <div className="ei-selectors">
              <RunSelect
                label="基线运行"
                value={baselineId}
                runs={runs}
                onChange={(value) => {
                  setBaselineId(value)
                  comparison.reset()
                }}
                exclude={candidateId}
              />
              <RunSelect
                label="候选运行"
                value={candidateId}
                runs={runs}
                onChange={(value) => {
                  setCandidateId(value)
                  comparison.reset()
                }}
                exclude={baselineId}
              />
              <button
                className="button-primary"
                type="button"
                disabled={!canCompare || comparison.isPending}
                onClick={() => {
                  setFormError('')
                  comparison.mutate()
                }}
              >
                {comparison.isPending ? '对比中…' : '调用服务端对比'}
              </button>
            </div>
            {baselineId && baselineId === candidateId && (
              <p className="ei-inline-note" role="alert">
                基线与候选不能是同一次运行。
              </p>
            )}
          </section>

          {comparison.isError && (
            <section className="ei-panel ei-panel--warning">
              <header>
                <div>
                  <h2>服务端对比未返回结果</h2>
                  <p>{describeError(comparison.error)}</p>
                </div>
              </header>
              <p className="ei-inline-note">
                已退回下方并排对比：数据直接取自两次运行的报告，不做额外聚合。
              </p>
            </section>
          )}

          {comparison.data && (
            <section className="ei-panel">
              <header>
                <div>
                  <h2>服务端对比结果</h2>
                  <p>
                    对比报告 ID：{comparison.data.comparison_id}。回归项：
                    {comparison.data.regressions.length > 0
                      ? comparison.data.regressions.join('、')
                      : '无'}
                  </p>
                </div>
              </header>
              {comparison.data.deltas.length === 0 ? (
                <EmptyState
                  title="没有指标差异"
                  description="服务端对比报告未返回任何指标增量。"
                />
              ) : (
                <div className="ei-table-wrap">
                  <table className="ei-table">
                    <thead>
                      <tr>
                        <th>指标</th>
                        <th>基线</th>
                        <th>候选</th>
                        <th>变化</th>
                        <th>是否回退</th>
                      </tr>
                    </thead>
                    <tbody>
                      {comparison.data.deltas.map((delta) => (
                        <tr
                          key={delta.metric}
                          className={delta.regressed ? 'ei-row--regressed' : undefined}
                        >
                          <td>{metricLabel(delta.metric)}</td>
                          <td>{metricValue(delta.metric, delta.baseline)}</td>
                          <td>{metricValue(delta.metric, delta.candidate)}</td>
                          <td>
                            {delta.delta > 0 ? '+' : ''}
                            {metricValue(delta.metric, delta.delta)}
                          </td>
                          <td>{delta.regressed ? '是' : '否'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          )}

          {canCompare && (
            <section className="ei-panel">
              <header>
                <div>
                  <h2>并排对比（原始报告）</h2>
                  <p>
                    指标原样来自两次评测运行；用例结果按 case_id 并排展示，不做跨运行聚合。
                  </p>
                </div>
              </header>
              <div className="ei-metrics">
                <MetricTile label="基线通过率" value={percent(baselineReport?.pass_rate)} />
                <MetricTile label="候选通过率" value={percent(candidateReport?.pass_rate)} />
                <MetricTile label="基线平均分" value={fixed(baselineReport?.average_score)} />
                <MetricTile label="候选平均分" value={fixed(candidateReport?.average_score)} />
                <MetricTile
                  label="基线平均召回率"
                  value={percent(baselineReport?.average_recall_at_k)}
                />
                <MetricTile
                  label="候选平均召回率"
                  value={percent(candidateReport?.average_recall_at_k)}
                />
              </div>
              <p className="ei-inline-note">
                基线门槛结果：{baseline?.gate_passed ? '通过' : '未通过'}；候选门槛结果：
                {candidate?.gate_passed ? '通过' : '未通过'}。基线数据集：
                {baseline?.dataset_id} v{baseline?.dataset_version ?? '—'}；候选数据集：
                {candidate?.dataset_id} v{candidate?.dataset_version ?? '—'}。
              </p>
              {caseRows.length === 0 ? (
                <EmptyState
                  title="运行报告没有用例明细"
                  description="服务端报告未返回 results；不会补造用例结果。"
                />
              ) : (
                <div className="ei-table-wrap">
                  <table className="ei-table">
                    <thead>
                      <tr>
                        <th rowSpan={2}>用例</th>
                        <th colSpan={2}>基线</th>
                        <th colSpan={2}>候选</th>
                      </tr>
                      <tr>
                        <th>分数</th>
                        <th>结果</th>
                        <th>分数</th>
                        <th>结果</th>
                      </tr>
                    </thead>
                    <tbody>
                      {caseRows.map((row) => (
                        <tr
                          key={row.caseId}
                          className={
                            row.baselineCase?.passed && row.candidateCase
                              ? !row.candidateCase.passed
                                ? 'ei-row--regressed'
                                : undefined
                              : undefined
                          }
                        >
                          <td>{row.caseId}</td>
                          <td>{fixed(row.baselineCase?.score)}</td>
                          <td>
                            {row.baselineCase
                              ? row.baselineCase.passed
                                ? '通过'
                                : '未通过'
                              : '—'}
                          </td>
                          <td>{fixed(row.candidateCase?.score)}</td>
                          <td>
                            {row.candidateCase
                              ? row.candidateCase.passed
                                ? '通过'
                                : '未通过'
                              : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          )}
        </>
      )}

      {tab === 'rollout' && (
        <>
          <section className="ei-panel">
            <header>
              <div>
                <h2>将评测实验灰度到 Prompt Lab</h2>
                <p>
                  服务端要求传入 Prompt 实验报告 ID，并按实验报告中的变体指标配置灰度比例与最小分数增益。
                </p>
              </div>
            </header>
            <p className="ei-inline-note">
              当前后端没有“实验报告列表”接口，因此此处需填写已有实验 ID（来自 Prompt 实验报告）；评测运行 ID
              不能替代实验 ID。
            </p>
            <div className="ei-form">
              <label className="ei-field">
                <span>实验 ID</span>
                <input
                  value={experimentId}
                  onChange={(event) => setExperimentId(event.target.value)}
                  placeholder="填写 Prompt 实验报告 ID"
                />
              </label>
              <label className="ei-field">
                <span>发布模式</span>
                <select
                  value={mode}
                  onChange={(event) =>
                    setMode(event.target.value === 'manual' ? 'manual' : 'automatic')
                  }
                >
                  <option value="automatic">自动选择胜出变体</option>
                  <option value="manual">手动指定变体</option>
                </select>
              </label>
              {mode === 'manual' && (
                <label className="ei-field">
                  <span>变体名称</span>
                  <input
                    value={variant}
                    onChange={(event) => setVariant(event.target.value)}
                    placeholder="必须是实验报告中存在的变体"
                  />
                </label>
              )}
              <label className="ei-field">
                <span>灰度比例（1–100）</span>
                <input
                  type="number"
                  min={1}
                  max={100}
                  step={1}
                  value={rolloutPercentage}
                  onChange={(event) => setRolloutPercentage(event.target.value)}
                />
              </label>
              <label className="ei-field">
                <span>最小分数增益</span>
                <input
                  type="number"
                  min={0}
                  step={0.01}
                  value={minimumScoreGain}
                  onChange={(event) => setMinimumScoreGain(event.target.value)}
                />
              </label>
              <button
                className="button-primary"
                type="button"
                disabled={rollout.isPending}
                onClick={submitRollout}
              >
                {rollout.isPending ? '提交中…' : '应用灰度发布'}
              </button>
            </div>
            <p className="ei-inline-note">
              服务端仅接受“灰度比例”和“最小分数增益”两个阈值；最小分数、最大回退等其它阈值未在该接口契约中提供，本页不伪造。
            </p>
            {(formError || rollout.isError) && (
              <p className="ei-inline-error" role="alert">
                {formError || describeError(rollout.error)}
              </p>
            )}
          </section>

          {rollout.data && (
            <section className="ei-panel">
              <header>
                <div>
                  <h2>灰度发布结果</h2>
                  <p>结果由服务端 Prompt Lab 返回，包含选中的变体与流量权重。</p>
                </div>
              </header>
              <dl className="ei-result">
                <div>
                  <dt>实验 ID</dt>
                  <dd>{rollout.data.experiment_id}</dd>
                </div>
                <div>
                  <dt>提示词标识</dt>
                  <dd>{rollout.data.prompt_id}</dd>
                </div>
                <div>
                  <dt>发布模式</dt>
                  <dd>{valueLabel(rollout.data.mode)}</dd>
                </div>
                <div>
                  <dt>选中变体</dt>
                  <dd>{rollout.data.selected_variant}</dd>
                </div>
                <div>
                  <dt>策略版本</dt>
                  <dd>{rollout.data.policy_revision}</dd>
                </div>
                <div>
                  <dt>流量权重</dt>
                  <dd>
                    {Object.entries(rollout.data.weights)
                      .map(([name, weight]) => `${name}：${fixed(weight)}%`)
                      .join(' · ') || '—'}
                  </dd>
                </div>
              </dl>
            </section>
          )}
        </>
      )}

      {tab === 'scores' && runs.length > 0 && (
        <>
          <section className="ei-panel">
            <header>
              <div>
                <h2>评分与用例明细</h2>
                <p>
                  用例结果直接来自评测运行报告；人工评分来自独立评分接口。评分录入入口：
                  {scoreRunId ? (
                    <Link
                      className="text-link"
                      to={`/operations/evaluation/runs/${encodeURIComponent(scoreRunId)}`}
                    >
                      评测运行详情页
                    </Link>
                  ) : (
                    '选择运行后可进入评测运行详情页'
                  )}
                  。
                </p>
              </div>
            </header>
            <div className="ei-selectors">
              <RunSelect
                label="评测运行"
                value={scoreRunId}
                runs={runs}
                onChange={(value) => setScoreRunId(value)}
              />
            </div>
          </section>

          {!scoreRunId && (
            <EmptyState
              title="选择一次评测运行"
              description="选择后可查看该运行的用例结果与人工评分明细。"
            />
          )}

          {scoreRunId && scoreRun && (
            <>
              <section className="ei-panel">
                <header>
                  <div>
                    <h2>运行摘要</h2>
                    <p>
                      {scoreRun.model_key || '未知模型'} · {scoreRun.dataset_id} v
                      {scoreRun.dataset_version ?? '—'} · {formatDateTime(scoreRun.created_at)}
                    </p>
                  </div>
                </header>
                <div className="ei-metrics">
                  <MetricTile label="通过率" value={percent(scoreReport?.pass_rate)} />
                  <MetricTile label="平均分" value={fixed(scoreReport?.average_score)} />
                  <MetricTile
                    label="平均召回率"
                    value={percent(scoreReport?.average_recall_at_k)}
                  />
                  <MetricTile
                    label="门槛结果"
                    value={scoreRun.gate_passed ? '通过' : '未通过'}
                  />
                </div>
                {Array.isArray(scoreReport?.gate_failures) &&
                  scoreReport.gate_failures.length > 0 && (
                    <p className="ei-inline-note">
                      门槛失败项：{scoreReport.gate_failures.join('、')}
                    </p>
                  )}
              </section>

              <section className="ei-panel">
                <header>
                  <div>
                    <h2>用例结果</h2>
                    <p>逐条展示服务端报告中的用例得分、通过状态与命中信息。</p>
                  </div>
                </header>
                {(scoreReport?.results ?? []).length === 0 ? (
                  <EmptyState
                    title="报告没有用例明细"
                    description="该运行报告未返回 results；不会补造用例结果。"
                  />
                ) : (
                  <div className="ei-table-wrap">
                    <table className="ei-table">
                      <thead>
                        <tr>
                          <th>用例</th>
                          <th>分数</th>
                          <th>结果</th>
                          <th>缺失术语</th>
                          <th>命中禁用词</th>
                          <th>召回率</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(scoreReport?.results ?? []).map((item) => (
                          <tr key={item.case_id}>
                            <td>{item.case_id}</td>
                            <td>{fixed(item.score)}</td>
                            <td>{item.passed ? '通过' : '未通过'}</td>
                            <td>{item.missing_terms?.join('、') || '—'}</td>
                            <td>{item.forbidden_terms_found?.join('、') || '—'}</td>
                            <td>{percent(item.recall_at_k)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>

              <section className="ei-panel">
                <header>
                  <div>
                    <h2>人工评分</h2>
                    <p>读取该运行的人工评分记录；录入请在评测运行详情页完成。</p>
                  </div>
                </header>
                {scores.isPending && <LoadingState label="正在读取人工评分" />}
                {scores.isError && (
                  <p className="ei-inline-error" role="alert">
                    {describeError(scores.error)}
                  </p>
                )}
                {scores.isSuccess && scoreItems.length === 0 && (
                  <EmptyState
                    title="暂无人工评分"
                    description="该运行还没有评审人提交的评分记录。"
                  />
                )}
                {scoreItems.length > 0 && (
                  <div className="ei-table-wrap">
                    <table className="ei-table">
                      <thead>
                        <tr>
                          <th>用例</th>
                          <th>评审人</th>
                          <th>分数</th>
                          <th>说明</th>
                          <th>更新时间</th>
                        </tr>
                      </thead>
                      <tbody>
                        {scoreItems.map((item) => (
                          <tr key={item.score_id}>
                            <td>{item.case_id}</td>
                            <td>{item.reviewer_id}</td>
                            <td>{fixed(item.score)}</td>
                            <td>{item.rationale || '—'}</td>
                            <td>{formatDateTime(item.updated_at ?? item.created_at)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            </>
          )}
        </>
      )}

      <p className="ei-footnote">
        说明：多模型变体评测接口（/observability/evaluations/variants/run）尚未在平台应用中挂载，本页不提供变体运行入口。评测报告与运行列表来自
        /observability/evaluations，对比接口按部署配置可能返回 503；此时仅展示两次运行的原始报告。
      </p>
    </section>
  )
}
