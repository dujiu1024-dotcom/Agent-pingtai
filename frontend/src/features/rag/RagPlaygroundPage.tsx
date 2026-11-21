import { useState } from 'react'
import type { FormEvent } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'

import { EmptyState, ErrorState, LoadingState } from '@/components/States'
import { ApiError, apiClient } from '@/lib/api'
import { localizedJson } from '@/lib/i18n/labels'
import './RagPlaygroundPage.css'

type RagOptions = {
  embedding_models: string[]
  default_embedding_model: string
  rerank_options: boolean[]
  max_top_k: number
}

type RetrievedHit = {
  document_id: string
  document_version: number | null
  chunk_id: string
  text: string
  score: number
  title: string | null
  source_uri: string
  source_url: string | null
  page_number: number | null
  start_offset: number | null
  end_offset: number | null
  source_locator: {
    page_number: number | null
    start_offset: number | null
    end_offset: number | null
  }
  retrieval_trace: Record<string, unknown>
  citation_id: string | null
}

type RagQueryResponse = {
  query: string
  embedding_model: string
  rerank: boolean
  top_k: number
  min_score: number
  answer: string
  abstained: boolean
  reason: string | null
  retrieved: RetrievedHit[]
  citations: Record<string, unknown>[]
}

type RagEvaluationCase = {
  case_id: string
  hit_rate_at_k: number | null
  reciprocal_rank: number | null
  ndcg_at_k: number | null
  token_coverage: number | null
  retrieved_source_uris: string[]
  retrieved_document_ids: string[]
}

type RagEvaluationReport = {
  dataset_id: string
  dataset_version: number
  dataset_fingerprint: string
  top_k: number
  embedding_model: string
  rerank: boolean
  min_score: number
  tokenizer: string
  evaluated_cases: number
  ranking_scored_cases: number
  token_scored_cases: number
  hit_rate_at_k: number | null
  mrr: number | null
  ndcg_at_k: number | null
  token_coverage: number | null
  cases: RagEvaluationCase[]
  run_fingerprint: string
}

type Tab = 'query' | 'evaluate'

const REASON_LABELS: Record<string, string> = {
  no_authorized_evidence: '没有可用的授权证据',
  empty_answer: '模型返回空答案',
  citations_required: '答案缺少引用',
  unknown_citation: '答案引用了未知来源',
}

const reasonLabel = (value: string | null) =>
  value ? REASON_LABELS[value] ?? value : '—'

const errorText = (error: unknown) => {
  if (error instanceof ApiError && error.status === 403)
    return '当前账号没有 RAG 检索或评测权限。'
  if (error instanceof ApiError && error.status === 503)
    return 'RAG 服务未启用，或检索/生成依赖暂不可用。'
  return error instanceof Error ? error.message : '请求失败，请稍后重试。'
}

const pct = (value: number | null | undefined) =>
  typeof value === 'number' ? `${(value * 100).toFixed(1)}%` : '—'

const numberText = (value: number | null | undefined) =>
  typeof value === 'number' ? value.toFixed(3) : '—'

const formatValue = (value: number | null | undefined) =>
  typeof value === 'number' ? value.toFixed(3) : '—'

export function RagPlaygroundPage() {
  const [tab, setTab] = useState<Tab>('query')
  const [query, setQuery] = useState('')
  const [topK, setTopK] = useState(5)
  const [minScore, setMinScore] = useState(0)
  const [embeddingModel, setEmbeddingModel] = useState('')
  const [rerank, setRerank] = useState(false)
  const [queryError, setQueryError] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [datasetText, setDatasetText] = useState('')
  const [evaluateError, setEvaluateError] = useState('')

  const optionsQuery = useQuery({
    queryKey: ['rag-playground', 'options'],
    queryFn: () =>
      apiClient.get<RagOptions>('/platform/v1/rag-playground/options'),
    retry: false,
  })

  const queryMutation = useMutation({
    mutationFn: (payload: {
      query: string
      top_k: number
      min_score: number
      embedding_model: string
      rerank: boolean
    }) => apiClient.post<RagQueryResponse>('/platform/v1/rag-playground/query', payload),
    onSuccess: () => setQueryError(''),
    onError: (error) => setQueryError(errorText(error)),
  })

  const evaluateMutation = useMutation({
    mutationFn: (payload: {
      dataset: Record<string, unknown>
      top_k: number
      min_score: number
      embedding_model: string
      rerank: boolean
    }) => apiClient.post<RagEvaluationReport>('/platform/v1/rag-playground/evaluate', payload),
    onSuccess: () => setEvaluateError(''),
    onError: (error) => setEvaluateError(errorText(error)),
  })

  const options = optionsQuery.data
  const defaultModel = options?.default_embedding_model ?? ''
  const activeModel = embeddingModel || defaultModel
  const maxTopK = options?.max_top_k ?? 20

  const validateSettings = () => {
    if (!activeModel) {
      return '请选择向量模型。'
    }
    if (!Number.isInteger(topK) || topK < 1 || topK > maxTopK) {
      return `top_k 必须是 1 到 ${maxTopK} 之间的整数。`
    }
    if (Number.isNaN(minScore) || minScore < 0 || minScore > 1) {
      return 'min_score 必须在 0 到 1 之间。'
    }
    return ''
  }

  const submitQuery = (event: FormEvent) => {
    event.preventDefault()
    setQueryError('')
    if (!query.trim()) {
      setQueryError('请输入检索问题。')
      return
    }
    const invalid = validateSettings()
    if (invalid) {
      setQueryError(invalid)
      return
    }
    queryMutation.mutate({
      query: query.trim(),
      top_k: topK,
      min_score: minScore,
      embedding_model: activeModel,
      rerank,
    })
  }

  const submitEvaluate = (event: FormEvent) => {
    event.preventDefault()
    setEvaluateError('')
    const invalid = validateSettings()
    if (invalid) {
      setEvaluateError(invalid)
      return
    }
    let parsed: unknown
    try {
      parsed = JSON.parse(datasetText)
    } catch {
      setEvaluateError('评测数据集不是合法的 JSON，请检查括号、引号与逗号。')
      return
    }
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
      setEvaluateError('评测数据集必须是 JSON 对象（包含 dataset_id、version、description、documents、cases）。')
      return
    }
    evaluateMutation.mutate({
      dataset: parsed as Record<string, unknown>,
      top_k: topK,
      min_score: minScore,
      embedding_model: activeModel,
      rerank,
    })
  }

  const toggleExpanded = (key: string) => {
    setExpanded((previous) => {
      const next = new Set(previous)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const result = queryMutation.data
  const report = evaluateMutation.data

  return (
    <section className="page-content rag-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">智能体运营 / RAG 实验台</p>
          <h1>RAG 实验台</h1>
          <p>使用指定向量模型检索知识库并生成带引用的答案，或对评测集计算检索指标。</p>
        </div>
      </div>

      {optionsQuery.isPending && <LoadingState label="正在加载 RAG 选项" />}

      {optionsQuery.isError && (
        <ErrorState
          title="RAG 选项加载失败"
          description={errorText(optionsQuery.error)}
          onRetry={() => void optionsQuery.refetch()}
        />
      )}

      {optionsQuery.isSuccess && options && (
        <>
          <section className="rag-settings">
            <label>
              向量模型
              <select
                value={activeModel}
                onChange={(event) => setEmbeddingModel(event.target.value)}
              >
                {options.embedding_models.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Top K
              <input
                type="number"
                min={1}
                max={maxTopK}
                value={topK}
                onChange={(event) => setTopK(Number(event.target.value))}
              />
            </label>
            <label>
              最低分数
              <input
                type="number"
                min={0}
                max={1}
                step={0.01}
                value={minScore}
                onChange={(event) => setMinScore(Number(event.target.value))}
              />
            </label>
            <label className="rag-checkbox">
              <input
                type="checkbox"
                checked={rerank}
                onChange={(event) => setRerank(event.target.checked)}
              />
              启用重排序
            </label>
          </section>

          <div className="panel-tabs" role="tablist">
            <button
              className={`panel-tab ${tab === 'query' ? 'active' : ''}`}
              type="button"
              role="tab"
              aria-selected={tab === 'query'}
              onClick={() => setTab('query')}
            >
              检索问答
            </button>
            <button
              className={`panel-tab ${tab === 'evaluate' ? 'active' : ''}`}
              type="button"
              role="tab"
              aria-selected={tab === 'evaluate'}
              onClick={() => setTab('evaluate')}
            >
              批量评测
            </button>
          </div>

          {tab === 'query' && (
            <>
              <form className="rag-query-form" onSubmit={submitQuery}>
                <label>
                  问题
                  <textarea
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    rows={4}
                    maxLength={4096}
                    placeholder="输入要检索的问题，例如：合同续签的审批流程是什么？"
                  />
                </label>
                {queryError && (
                  <p className="rag-error" role="alert">
                    {queryError}
                  </p>
                )}
                <div className="rag-form-actions">
                  <button
                    className="button-primary"
                    type="submit"
                    disabled={queryMutation.isPending}
                  >
                    {queryMutation.isPending ? '检索中…' : '发起检索'}
                  </button>
                  <span className="rag-hint">
                    检索与生成均走服务端 RAG 服务，答案基于授权知识库。
                  </span>
                </div>
              </form>

              {queryMutation.isPending && <LoadingState label="正在检索并生成答案" />}

              {result && (
                <section className="rag-result">
                  <header className="rag-result-head">
                    <div>
                      <h2>答案</h2>
                      <p>
                        {result.embedding_model} · Top {result.top_k} · 最低分{' '}
                        {result.min_score} · {result.rerank ? '已重排序' : '未重排序'}
                      </p>
                    </div>
                    <span
                      className={`rag-abstain ${result.abstained ? 'abstained' : 'answered'}`}
                    >
                      {result.abstained ? '已弃答' : '已生成'}
                    </span>
                  </header>

                  {result.abstained ? (
                    <p className="rag-abstain-note">
                      未生成答案：{reasonLabel(result.reason)}
                    </p>
                  ) : (
                    <p className="rag-answer-text">{result.answer}</p>
                  )}

                  <div className="rag-hit-block">
                    <h3>命中片段（{result.retrieved.length}）</h3>
                    {result.retrieved.length === 0 ? (
                      <p className="rag-hint">本次检索没有命中任何片段。</p>
                    ) : (
                      <ul className="rag-hit-list">
                        {result.retrieved.map((hit, index) => {
                          const key =
                            hit.chunk_id || hit.citation_id || `hit-${index}`
                          const isLong = hit.text.length > 240
                          const visible =
                            expanded.has(key) || !isLong
                              ? hit.text
                              : `${hit.text.slice(0, 240)}…`
                          return (
                            <li key={key}>
                              <div className="rag-hit-head">
                                <strong>{hit.title ?? '未命名片段'}</strong>
                                <span className="rag-score">
                                  分数 {formatValue(hit.score)}
                                </span>
                              </div>
                              <p className="rag-hit-meta">
                                文档 {hit.document_id} · 分块 {hit.chunk_id}
                                {hit.page_number !== null && ` · 第 ${hit.page_number} 页`}
                                {hit.citation_id && ` · 引用 ${hit.citation_id}`}
                              </p>
                              <p className="rag-hit-text">{visible}</p>
                              {isLong && (
                                <button
                                  className="rag-toggle"
                                  type="button"
                                  onClick={() => toggleExpanded(key)}
                                >
                                  {expanded.has(key) ? '收起' : '展开全文'}
                                </button>
                              )}
                              <details className="rag-hit-json">
                                <summary>查看片段来源</summary>
                                <pre>{localizedJson(hit)}</pre>
                              </details>
                            </li>
                          )
                        })}
                      </ul>
                    )}
                  </div>

                  {result.citations.length > 0 && (
                    <details className="rag-citations">
                      <summary>引用（{result.citations.length}）</summary>
                      <pre>{localizedJson(result.citations)}</pre>
                    </details>
                  )}
                </section>
              )}
            </>
          )}

          {tab === 'evaluate' && (
            <>
              <form className="rag-eval-form" onSubmit={submitEvaluate}>
                <label>
                  评测数据集（JSON 对象）
                  <textarea
                    value={datasetText}
                    onChange={(event) => setDatasetText(event.target.value)}
                    rows={12}
                    spellCheck={false}
                    placeholder={`{
  "dataset_id": "demo-dataset",
  "version": 1,
  "description": "示例评测集",
  "documents": [
    {
      "key": "doc-1",
      "title": "文档标题",
      "source_uri": "https://example.com/doc-1",
      "content": "文档正文"
    }
  ],
  "cases": [
    {
      "case_id": "case-1",
      "query": "问题",
      "expected_document_keys": ["doc-1"],
      "expected_answer_terms": ["关键词"],
      "answer": "参考答案",
      "expect_abstain": false
    }
  ]
}`}
                  />
                </label>
                <p className="rag-hint">
                  数据集必须是对象，字段固定为 dataset_id、version、description、documents、cases；
                  最多 100 个用例、1000 篇文档。评分基于文档级检索结果与参考答案词覆盖。
                </p>
                {evaluateError && (
                  <p className="rag-error" role="alert">
                    {evaluateError}
                  </p>
                )}
                <div className="rag-form-actions">
                  <button
                    className="button-primary"
                    type="submit"
                    disabled={evaluateMutation.isPending}
                  >
                    {evaluateMutation.isPending ? '评测中…' : '运行评测'}
                  </button>
                </div>
              </form>

              {evaluateMutation.isPending && <LoadingState label="正在运行批量评测" />}

              {report && (
                <section className="rag-report">
                  <header className="rag-result-head">
                    <div>
                      <h2>评测结果</h2>
                      <p>
                        {report.dataset_id} v{report.dataset_version} · 用例{' '}
                        {report.evaluated_cases} 个 · Top {report.top_k}
                      </p>
                    </div>
                  </header>

                  <div className="rag-metrics">
                    <article>
                      <span>命中率 @K</span>
                      <strong>{pct(report.hit_rate_at_k)}</strong>
                    </article>
                    <article>
                      <span>MRR</span>
                      <strong>{numberText(report.mrr)}</strong>
                    </article>
                    <article>
                      <span>nDCG @K</span>
                      <strong>{numberText(report.ndcg_at_k)}</strong>
                    </article>
                    <article>
                      <span>词覆盖率</span>
                      <strong>{pct(report.token_coverage)}</strong>
                    </article>
                    <article>
                      <span>排序计分用例</span>
                      <strong>{report.ranking_scored_cases}</strong>
                    </article>
                    <article>
                      <span>词覆盖计分用例</span>
                      <strong>{report.token_scored_cases}</strong>
                    </article>
                  </div>

                  <details className="rag-report-json">
                    <summary>查看完整评测报告</summary>
                    <pre>{localizedJson(report)}</pre>
                  </details>
                </section>
              )}

              {!report && !evaluateMutation.isPending && !evaluateError && (
                <EmptyState
                  title="尚未运行评测"
                  description="粘贴完整评测集后运行，将返回命中率、MRR、nDCG 与词覆盖率等指标。"
                />
              )}
            </>
          )}
        </>
      )}
    </section>
  )
}
