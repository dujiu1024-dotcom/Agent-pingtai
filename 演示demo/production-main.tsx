import React from 'react'
import ReactDOM from 'react-dom/client'

import '@/components/design-system.css'
import '@/theme.css'
import '@/styles.css'

type JsonRecord = Record<string, unknown>

const now = new Date().toISOString()
const agents = [
  {
    item_id: 'agent-orchestrator',
    status: 'published',
    created_at: now,
    published_at: now,
    definition: {
      name: '总控调度 Agent',
      description: '负责任务拆解、路由与执行协调',
      version: '1.4.0',
    },
  },
  {
    item_id: 'agent-researcher',
    status: 'published',
    created_at: now,
    published_at: now,
    definition: {
      name: '研究分析 Agent',
      description: '负责资料检索、分析与结构化总结',
      version: '1.2.0',
    },
  },
  {
    item_id: 'agent-engineer',
    status: 'draft',
    created_at: now,
    definition: {
      name: '工程执行 Agent',
      description: '负责代码实现、测试与交付',
      version: '0.9.0',
    },
  },
]

const tasks = [
  {
    task_id: 'task-demo-2026-001',
    title: '完成产品需求分析与方案拆解',
    description: '整理需求边界，形成可执行任务清单',
    status: 'running',
    priority: 'high',
    agent_id: 'agent-researcher',
    run_id: 'run-demo-2026-001',
    created_at: now,
    updated_at: now,
  },
  {
    task_id: 'task-demo-2026-002',
    title: '生成前端模块回归测试',
    description: '覆盖导航、列表筛选和详情交互',
    status: 'waiting_approval',
    priority: 'normal',
    agent_id: 'agent-engineer',
    run_id: 'run-demo-2026-002',
    created_at: now,
    updated_at: now,
  },
  {
    task_id: 'task-demo-2026-003',
    title: '同步模型资源目录',
    description: '校验 Provider 与模型模板状态',
    status: 'completed',
    priority: 'low',
    agent_id: 'agent-orchestrator',
    run_id: 'run-demo-2026-003',
    created_at: now,
    updated_at: now,
    finished_at: now,
  },
]

const collections: Record<string, unknown[]> = {
  '/platform/v1/workbench/tasks': tasks,
  '/platform/v1/registry/agent': agents,
  '/platform/v1/registry/workflow': [
    {
      item_id: 'workflow-release-review',
      status: 'published',
      definition: { name: '版本发布评审', description: '发布前检查、审批与通知' },
      created_at: now,
      published_at: now,
    },
  ],
  '/platform/v1/approvals': [
    {
      approval_id: 'approval-demo-001',
      action: '发布 Agent 新版本',
      resource: 'agent-engineer',
      status: 'pending',
      created_at: now,
    },
  ],
  '/platform/v1/registry/tool': [
    { item_id: 'tool-web-search', status: 'active', name: 'Web Search', kind: 'mcp' },
    { item_id: 'tool-file-reader', status: 'active', name: 'File Reader', kind: 'builtin' },
  ],
  '/platform/v1/registry/skill': [
    { item_id: 'skill-analysis', status: 'published', name: '分析与归纳' },
    { item_id: 'skill-code-review', status: 'published', name: '代码审查' },
  ],
  '/platform/v1/runs': [
    { run_id: 'run-demo-2026-001', task_id: 'task-demo-2026-001', status: 'running', created_at: now },
    { run_id: 'run-demo-2026-002', task_id: 'task-demo-2026-002', status: 'waiting_approval', created_at: now },
  ],
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(status === 204 ? null : JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  })
}

const storageKey = 'agent-platform-demo-api-v1'
const idFields = ['task_id', 'item_id', 'approval_id', 'run_id', 'id'] as const

function readCollections(): Record<string, JsonRecord[]> {
  try {
    const saved = window.localStorage.getItem(storageKey)
    if (saved) {
      const parsed: unknown = JSON.parse(saved)
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed))
        return parsed as Record<string, JsonRecord[]>
    }
  } catch {
    // Private browsing or disabled storage: retain this page's in-memory demo data.
  }
  return Object.fromEntries(
    Object.entries(collections).map(([path, items]) => [
      path,
      items.map((item) => ({ ...(item as JsonRecord) })),
    ]),
  )
}

let demoCollections = readCollections()

function persistCollections(): void {
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(demoCollections))
  } catch {
    // The demo remains interactive for the current page session without storage.
  }
}

function getItemId(item: JsonRecord): string | undefined {
  for (const field of idFields) {
    const value = item[field]
    if (typeof value === 'string' && value) return value
  }
  return undefined
}

function responseForCollection(items: JsonRecord[], url: URL): Response {
  let filtered = [...items]
  const status = url.searchParams.get('status')
  const query = url.searchParams.get('q') ?? url.searchParams.get('search')
  if (status) filtered = filtered.filter((item) => item.status === status)
  if (query) {
    const needle = query.toLocaleLowerCase()
    filtered = filtered.filter((item) =>
      JSON.stringify(item).toLocaleLowerCase().includes(needle),
    )
  }
  const total = filtered.length
  const limit = Number(url.searchParams.get('limit') ?? url.searchParams.get('page_size'))
  const page = Math.max(1, Number(url.searchParams.get('page') ?? 1) || 1)
  if (Number.isFinite(limit) && limit > 0)
    filtered = filtered.slice((page - 1) * limit, page * limit)
  return jsonResponse({ items: filtered, total })
}

type DemoFixture = JsonRecord & { items?: JsonRecord[]; total?: number; count?: number }

function fixtureForPath(path: string): DemoFixture {
  const now = new Date().toISOString()
  const common = (id: string, name: string, status = 'active') => ({
    id, item_id: id, name, title: name, display_name: name, status,
    description: name + ' · 本地演示样例', created_at: now, updated_at: now,
  })
  if (path.endsWith('/observability/metrics') && !path.endsWith('/metrics/json')) {
    return { __text: '# HELP http_requests_total Total HTTP requests\n# TYPE http_requests_total counter\nhttp_requests_total{method="GET"} 128430\n# HELP scheduler_jobs_active Active scheduler jobs\n# TYPE scheduler_jobs_active gauge\nscheduler_jobs_active 2\n# HELP process_resident_memory_bytes Resident memory\nprocess_resident_memory_bytes 268435456\n' }
  }
  if (path.includes('/graph/extractions')) return {
    items: [{
      job_id: 'graph-extraction-001', status: 'succeeded', total: 12, processed: 12,
      entity_count: 38, relation_count: 24, skipped: 0, error: null,
      created_at: now, updated_at: now,
    }], total: 1, count: 1,
  }
  if (path.includes('/graph/entities/') && path.includes('/neighbors')) {
    const entities = [
      { entity_id: 'entity-product', entity_type: 'concept', name: '产品平台', attributes: { domain: '产品' }, project_id: 'project-demo', source_ref: null },
      { entity_id: 'entity-agent', entity_type: 'concept', name: '智能体', attributes: { domain: 'Agent' }, project_id: 'project-demo', source_ref: null },
      { entity_id: 'entity-workflow', entity_type: 'concept', name: '工作流', attributes: { domain: '流程' }, project_id: 'project-demo', source_ref: null },
    ]
    return { entities, relations: [
      { relation_id: 'rel-001', relation_type: 'contains', source_entity_id: 'entity-product', target_entity_id: 'entity-agent', project_id: 'project-demo', attributes: {} },
      { relation_id: 'rel-002', relation_type: 'orchestrates', source_entity_id: 'entity-agent', target_entity_id: 'entity-workflow', project_id: 'project-demo', attributes: {} },
    ], truncated: false, visited_count: 3 }
  }
  if (path.includes('/graph/entities')) return {
    items: [
      { entity_id: 'entity-product', entity_type: 'concept', name: '产品平台', attributes: { domain: '产品', owner: '产品团队' }, project_id: 'project-demo', source_ref: null },
      { entity_id: 'entity-agent', entity_type: 'concept', name: '智能体', attributes: { domain: 'Agent', count: 12 }, project_id: 'project-demo', source_ref: null },
      { entity_id: 'entity-workflow', entity_type: 'concept', name: '工作流', attributes: { domain: '流程', count: 8 }, project_id: 'project-demo', source_ref: null },
    ], limit: 50,
  }
  if (path.includes('/graph/metrics')) return {
    query: '智能体', sampled: 3, entity_count: 3, relation_count: 2,
    isolated_entity_count: 0, isolated_entity_ratio: 0, relation_density: 0.67,
    active_subgraph_count: 1, truncated: false,
  }
  if (path.endsWith('/memories/candidates')) return {
    items: [
      { memory_id: 'memory-candidate-001', scope: 'project', scope_id: 'project-demo', content: '平台采用模块化 Agent 协作架构', source_ref: 'session-001', confidence: 0.96, conflict_key: null, status: 'candidate', version: 1 },
      { memory_id: 'memory-candidate-002', scope: 'user', scope_id: 'user-demo', content: '偏好使用中文进行项目沟通', source_ref: 'session-002', confidence: 0.93, conflict_key: null, status: 'candidate', version: 1 },
      { memory_id: 'memory-conflict-001', scope: 'project', scope_id: 'project-demo', content: '默认部署环境为容器集群', source_ref: 'session-003', confidence: 0.82, conflict_key: 'deployment-target', status: 'conflict', version: 2 },
      { memory_id: 'memory-conflict-002', scope: 'project', scope_id: 'project-demo', content: '默认部署环境为虚拟机', source_ref: 'session-004', confidence: 0.78, conflict_key: 'deployment-target', status: 'conflict', version: 1 },
    ], total: 4,
  }
  if (path.endsWith('/memories/export')) return {
    items: [
      { memory_id: 'memory-active-001', scope: 'project', scope_id: 'project-demo', content: '平台采用模块化 Agent 协作架构', status: 'active' },
      { memory_id: 'memory-active-002', scope: 'user', scope_id: 'user-demo', content: '偏好使用中文进行项目沟通', status: 'active' },
    ], total: 2,
  }
  if (path.includes('/memories/conflicts/resolve') || /\/memories\/[^/]+\/(confirm|archive)$/.test(path)) {
    return { memory_id: 'memory-active-001', status: 'active', version: 2, ok: true }
  }
  if (path.includes('/memories')) return {
    items: [
      { memory_id: 'memory-active-001', scope: 'project', scope_id: 'project-demo', content: '平台采用模块化 Agent 协作架构', source_ref: 'session-001', confidence: 0.96, conflict_key: null, status: 'active', version: 2, expires_at: null },
      { memory_id: 'memory-active-002', scope: 'user', scope_id: 'user-demo', content: '偏好使用中文进行项目沟通', source_ref: 'session-002', confidence: 0.93, conflict_key: null, status: 'active', version: 1, expires_at: null },
      { memory_id: 'memory-active-003', scope: 'agent', scope_id: 'agent-orchestrator', content: '任务拆分后由专职 Agent 执行', source_ref: 'session-003', confidence: 0.89, conflict_key: null, status: 'archived', version: 1, expires_at: null },
    ], total: 3,
  }
  if (path.includes('/observability/evaluations/') && path.endsWith('/scores')) return {
    items: [
      { score_id: 'score-001', eval_run_id: 'eval-run-001', case_id: 'case-001', reviewer_id: 'reviewer-demo', score: 0.96, rationale: '回答准确且引用完整', created_at: now },
      { score_id: 'score-002', eval_run_id: 'eval-run-001', case_id: 'case-002', reviewer_id: 'reviewer-demo', score: 0.91, rationale: '结论正确，细节可补充', created_at: now },
    ],
  }
  if (path.includes('/observability/evaluations/') && path.endsWith('/compare')) return {
    comparison_id: 'comparison-demo-001', baseline_run_id: 'eval-run-001', candidate_run_id: 'eval-run-002',
    regressions: [], deltas: [
      { metric: 'pass_rate', baseline: 0.91, candidate: 0.94, delta: 0.03, regressed: false },
      { metric: 'average_score', baseline: 0.88, candidate: 0.92, delta: 0.04, regressed: false },
    ],
  }
  if (path.includes('/evaluations/') && path.endsWith('/rollout')) return {
    experiment_id: 'experiment-demo-001', prompt_id: 'prompt-planner', selected_variant: 'candidate',
    mode: 'weighted', weights: { baseline: 20, candidate: 80 }, policy_revision: 3,
  }
  if (path.includes('/observability/evaluations') || path.includes('/evaluations')) return {
    items: [
      { eval_run_id: 'eval-run-001', dataset_id: 'dataset-support', dataset_version: 3, model_key: 'gpt-demo', gate_passed: true, created_at: now, report: { pass_rate: 0.94, average_score: 0.92, average_recall_at_k: 0.89, metrics: { latency_ms: 420, cost_usd: 0.012 }, results: [{ case_id: 'case-001', passed: true, score: 0.96 }, { case_id: 'case-002', passed: true, score: 0.91 }], gate_failures: [] } },
      { eval_run_id: 'eval-run-002', dataset_id: 'dataset-tools', dataset_version: 2, model_key: 'claude-demo', gate_passed: true, created_at: now, report: { pass_rate: 0.91, average_score: 0.89, metrics: { latency_ms: 510, cost_usd: 0.015 }, results: [{ case_id: 'case-003', passed: true, score: 0.89 }], gate_failures: [] } },
    ], total: 2,
  }
  if (path.includes('/sessions/') && path.endsWith('/messages')) return {
    items: [
      { message_id: 'message-001', session_id: 'session-001', role: 'user', content: '请整理当前产品需求和关键风险。', created_at: now },
      { message_id: 'message-002', session_id: 'session-001', role: 'assistant', content: '已完成需求归纳，识别出 12 项需求和 4 项重点风险。', created_at: now },
    ], total: 2,
  }
  if (path.includes('/sessions') || path.includes('/conversations')) return {
    items: [
      { session_id: 'session-001', title: '产品需求讨论', status: 'active', message_count: 18, agent_name: '总控调度 Agent', updated_at: now },
      { session_id: 'session-002', title: '代码审查与修复', status: 'completed', message_count: 32, agent_name: '工程执行 Agent', updated_at: now },
      { session_id: 'session-003', title: '知识库资料整理', status: 'active', message_count: 12, agent_name: '研究分析 Agent', updated_at: now },
    ], total: 3,
  }
  if (path.includes('/files')) return {
    items: [
      { asset_id: 'file-001', file_id: 'file-001', filename: '需求分析报告.pdf', name: '需求分析报告.pdf', mime_type: 'application/pdf', size: 248320, version: 1, session_id: 'session-001', created_at: now },
      { asset_id: 'file-002', file_id: 'file-002', filename: '回归测试结果.xlsx', name: '回归测试结果.xlsx', mime_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', size: 86400, version: 2, session_id: 'session-001', created_at: now },
      { asset_id: 'file-003', file_id: 'file-003', filename: '产品演示截图.png', name: '产品演示截图.png', mime_type: 'image/png', size: 512000, version: 1, session_id: 'session-003', created_at: now },
    ], total: 3,
  }
  if (path.includes('/observability/metrics')) return {
    items: [
      { name: '请求总量', metric: 'http_requests_total', value: 128430, unit: '次' },
      { name: '平均响应时间', metric: 'http_request_duration_ms', value: 126, unit: 'ms' },
      { name: '活跃 Agent', metric: 'agents_active', value: 8, unit: '个' },
      { name: '任务成功率', metric: 'task_success_ratio', value: '98.6', unit: '%' },
    ], total: 4, count: 4,
  }
  if (path.includes('/health/') || path.endsWith('/health/ready') || path.endsWith('/ready')) {
    return { status: 'healthy', checks: { api: 'ok', database: 'ok', redis: 'ok', scheduler: 'ok', storage: 'ok' }, reason: '演示环境模拟健康状态' }
  }
  if (path.includes('/scheduler/jobs')) return {
    items: [
      { job_id: 'job-cleanup', name: '历史记录清理', enabled: true, status: 'scheduled', interval_seconds: 3600 },
      { job_id: 'job-sync', name: '资源目录同步', enabled: true, status: 'running', interval_seconds: 900 },
      { job_id: 'job-report', name: '日报生成', enabled: false, status: 'paused', interval_seconds: 86400 },
    ], total: 3, count: 3,
  }
  if (path.includes('/scheduler/runs')) return {
    items: [
      { run_id: 'scheduler-run-001', job_id: 'job-sync', status: 'completed', started_at: now, finished_at: now },
      { run_id: 'scheduler-run-002', job_id: 'job-cleanup', status: 'running', started_at: now },
    ], total: 2, count: 2,
  }
  if (path.includes('/knowledge')) return {
    items: [
      { ...common('kb-product', '产品知识库', 'ready'), collection_id: 'kb-product', document_count: 128, chunk_count: 2460, index_status: 'ready' },
      { ...common('kb-tech', '技术文档库', 'indexing'), collection_id: 'kb-tech', document_count: 86, chunk_count: 1732, index_status: 'indexing' },
      { ...common('kb-support', '客户支持知识库', 'ready'), collection_id: 'kb-support', document_count: 54, chunk_count: 980, index_status: 'ready' },
    ], total: 3, count: 3,
  }
  if (path.includes('/memories')) return {
    items: [
      { ...common('memory-001', '用户偏好与工作习惯', 'confirmed'), memory_id: 'memory-001', category: 'preference', confidence: 0.96 },
      { ...common('memory-002', '项目背景与目标', 'pending'), memory_id: 'memory-002', category: 'project', confidence: 0.91 },
      { ...common('memory-003', '常用工具链', 'archived'), memory_id: 'memory-003', category: 'workflow', confidence: 0.88 },
    ], total: 3, count: 3,
  }
  if (path.includes('/sessions') || path.includes('/conversations')) return {
    items: [
      { ...common('session-001', '产品需求讨论'), session_id: 'session-001', message_count: 18, agent_name: '总控调度 Agent' },
      { ...common('session-002', '代码审查与修复', 'completed'), session_id: 'session-002', message_count: 32, agent_name: '工程执行 Agent' },
      { ...common('session-003', '知识库资料整理'), session_id: 'session-003', message_count: 12, agent_name: '研究分析 Agent' },
    ], total: 3, count: 3,
  }
  if (path.includes('/files')) return {
    items: [
      { ...common('file-001', '需求分析报告.pdf'), file_id: 'file-001', filename: '需求分析报告.pdf', mime_type: 'application/pdf', size: 248320, type: 'document' },
      { ...common('file-002', '回归测试结果.xlsx'), file_id: 'file-002', filename: '回归测试结果.xlsx', mime_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', size: 86400, type: 'spreadsheet' },
      { ...common('file-003', '产品演示截图.png'), file_id: 'file-003', filename: '产品演示截图.png', mime_type: 'image/png', size: 512000, type: 'image' },
    ], total: 3, count: 3,
  }
  if (path.includes('/evaluations') || path.includes('/evaluation')) return {
    items: [
      { ...common('eval-dataset-001', '客服问答基准集', 'ready'), dataset_id: 'eval-dataset-001', eval_run_id: 'eval-run-001', case_count: 240, score: 0.94, pass_rate: 94 },
      { ...common('eval-dataset-002', '工具调用准确率测试', 'completed'), dataset_id: 'eval-dataset-002', eval_run_id: 'eval-run-002', case_count: 180, score: 0.91, pass_rate: 91 },
      { ...common('eval-dataset-003', '复杂任务规划评测', 'running'), dataset_id: 'eval-dataset-003', eval_run_id: 'eval-run-003', case_count: 96, score: 0.89, pass_rate: 89 },
    ], total: 3, count: 3,
  }
  if (path.includes('/blackboard')) return {
    items: [
      { ...common('blackboard-001', '需求拆解结果', 'published'), entry_id: 'blackboard-001', owner: '研究分析 Agent', content: '已整理 12 项需求与验收标准' },
      { ...common('blackboard-002', '技术方案草稿', 'shared'), entry_id: 'blackboard-002', owner: '工程执行 Agent', content: '前后端模块边界与接口方案' },
      { ...common('blackboard-003', '风险清单', 'review'), entry_id: 'blackboard-003', owner: '总控调度 Agent', content: '识别 4 项需关注风险' },
    ], total: 3, count: 3,
  }
  if (path.includes('/registry/model') || path.includes('/models')) return {
    items: [
      { ...common('model-provider-openai', 'OpenAI'), provider_id: 'model-provider-openai', version: 3, model: 'GPT-6' },
      { ...common('model-provider-anthropic', 'Anthropic'), provider_id: 'model-provider-anthropic', version: 2, model: 'Claude' },
      { ...common('model-provider-local', '本地模型服务', 'draft'), provider_id: 'model-provider-local', version: 1, model: 'Qwen' },
    ], total: 3, count: 3,
  }
  if (path.includes('/prompts')) return {
    items: [
      { ...common('prompt-planner', '任务规划 Prompt', 'published'), prompt_id: 'prompt-planner', version: '2.1.0' },
      { ...common('prompt-reviewer', '代码审查 Prompt', 'published'), prompt_id: 'prompt-reviewer', version: '1.8.0' },
      { ...common('prompt-support', '客服应答 Prompt', 'draft'), prompt_id: 'prompt-support', version: '0.9.0' },
    ], total: 3, count: 3,
  }
  if (path.includes('/organization') || path.includes('/members') || path.includes('/rbac') || path.includes('/roles')) return {
    items: [
      { ...common('member-admin', '平台管理员'), member_id: 'member-admin', role: '管理员', email: 'admin@example.demo' },
      { ...common('member-operator', '运营专员'), member_id: 'member-operator', role: '运营人员', email: 'operator@example.demo' },
      { ...common('member-developer', '研发工程师'), member_id: 'member-developer', role: '开发人员', email: 'developer@example.demo' },
    ], total: 3, count: 3,
  }
  if (path.includes('/bootstrap')) return { auth: { enabled: true }, version: 'demo-local-fixtures', capabilities: ['prompt_management', 'prompt_evaluation_gate'] }
  return {
    items: [
      { ...common('demo-record-001', '演示记录 A'), version: 1, owner: '平台运营组' },
      { ...common('demo-record-002', '演示记录 B', 'pending'), version: 2, owner: '研发团队' },
      { ...common('demo-record-003', '演示记录 C', 'completed'), version: 1, owner: '业务团队' },
    ], total: 3, count: 3,
  }
}

const fixtureStorageKey = 'agent-platform-demo-fixtures-v1'

function readFixtureItems(key: string, fallback: JsonRecord[]): JsonRecord[] {
  try {
    const saved = window.localStorage.getItem(fixtureStorageKey)
    if (saved) {
      const parsed: unknown = JSON.parse(saved)
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        const items = (parsed as Record<string, unknown>)[key]
        if (Array.isArray(items)) return items as JsonRecord[]
      }
    }
  } catch {
    // Local fixture state is optional in private browsing.
  }
  return fallback.map((item) => ({ ...item }))
}

function writeFixtureItems(key: string, items: JsonRecord[]): void {
  try {
    const saved = window.localStorage.getItem(fixtureStorageKey)
    const parsed: Record<string, JsonRecord[]> = saved
      ? JSON.parse(saved) as Record<string, JsonRecord[]>
      : {}
    parsed[key] = items
    window.localStorage.setItem(fixtureStorageKey, JSON.stringify(parsed))
  } catch {
    // Keep the current page usable when storage is unavailable.
  }
}

function localFallback(input: RequestInfo | URL, init: RequestInit | undefined, path: string, method: string): Response {
  const url = new URL(input instanceof Request ? input.url : String(input), window.location.origin)
  const fixture = fixtureForPath(path)
  if (method === 'GET') {
    if (typeof fixture.__text === 'string') {
      return new Response(fixture.__text, { status: 200, headers: { 'Content-Type': 'text/plain; version=0.0.4; charset=utf-8' } })
    }
    if (path.endsWith('/active') || path.endsWith('/latest') || path.endsWith('/current')) {
      const first = fixture.items?.[0]
      return jsonResponse(first ?? fixture)
    }
    if (path.includes('/search') || path.includes('/answer')) {
      return jsonResponse({ ...fixture, answer: '根据演示数据检索，已整理出相关内容。', results: fixture.items ?? [] })
    }
    const fixtureKey = path.endsWith('/memories/candidates')
      ? '/platform/v1/memories/candidates'
      : path.endsWith('/memories/export')
        ? '/platform/v1/memories/export'
        : path.includes('/memories')
          ? '/platform/v1/memories'
          : path
    const items = fixture.items ? readFixtureItems(fixtureKey, fixture.items) : []
    const status = url.searchParams.get('status')
    const query = url.searchParams.get('q') ?? url.searchParams.get('search')
    let filtered = items.filter(item => (!status || item.status === status) &&
      (!query || JSON.stringify(item).toLowerCase().includes(query.toLowerCase())))
    const total = filtered.length
    const limit = Number(url.searchParams.get('limit') ?? url.searchParams.get('page_size'))
    const page = Math.max(1, Number(url.searchParams.get('page') ?? 1) || 1)
    if (Number.isFinite(limit) && limit > 0) filtered = filtered.slice((page - 1) * limit, page * limit)
    return jsonResponse({ ...fixture, items: filtered, total, count: total })
  }
  let body: JsonRecord = {}
  const raw = init?.body
  if (typeof raw === 'string' && raw.trim()) {
    try { const parsed: unknown = JSON.parse(raw); if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) body = parsed as JsonRecord } catch { /* demo accepts form-like payloads */ }
  }
  if (path.includes('/memories/')) {
    const candidateKey = '/platform/v1/memories/candidates'
    const activeKey = '/platform/v1/memories'
    const candidates = readFixtureItems(candidateKey, fixtureForPath(candidateKey).items ?? [])
    const active = readFixtureItems(activeKey, fixtureForPath(activeKey).items ?? [])
    const memoryId = decodeURIComponent(path.split('/memories/')[1]?.split('/')[0] ?? '')
    if (method === 'DELETE') {
      writeFixtureItems(candidateKey, candidates.filter((item) => item.memory_id !== memoryId))
      writeFixtureItems(activeKey, active.filter((item) => item.memory_id !== memoryId))
      return jsonResponse({ ok: true, deleted: true, demo: true })
    }
    if (method === 'POST') {
      if (path.endsWith('/conflicts/resolve')) {
        const selectedId = typeof body.selected_memory_id === 'string' ? body.selected_memory_id : ''
        const updated = candidates.filter((item) => item.conflict_key !== body.conflict_key)
        const selected = candidates.find((item) => item.memory_id === selectedId)
        if (selected) active.unshift({ ...selected, status: 'active', version: Number(selected.version ?? 1) + 1 })
        writeFixtureItems(candidateKey, updated)
        writeFixtureItems(activeKey, active)
        return jsonResponse({ memory_id: selectedId || 'memory-active-001', status: 'active', version: 2, ok: true, demo: true })
      }
      const candidate = candidates.find((item) => item.memory_id === memoryId)
      if (candidate) {
        const archived = path.endsWith('/archive')
        const updated = { ...candidate, status: archived ? 'archived' : 'active', version: Number(candidate.version ?? 1) + 1 }
        writeFixtureItems(candidateKey, candidates.filter((item) => item.memory_id !== memoryId))
        writeFixtureItems(activeKey, [updated, ...active.filter((item) => item.memory_id !== memoryId)])
        return jsonResponse({ memory_id: memoryId, status: updated.status, version: updated.version, ok: true, demo: true })
      }
    }
  }
  if (method === 'DELETE') return jsonResponse({ ok: true, deleted: true, demo: true })
  if (method === 'POST' && path.includes('/graph/extractions')) {
    return jsonResponse({ job_id: 'graph-extraction-demo-' + crypto.randomUUID(), total: 12, status: 'queued', demo: true }, 202)
  }
  if (method === 'POST' && (path.includes('/memories/') || path.includes('/memories/conflicts/'))) {
    return jsonResponse({ memory_id: 'memory-active-001', status: 'active', version: 2, ok: true, demo: true })
  }
  if (method === 'POST' && path.endsWith('/compare')) return jsonResponse(fixture)
  if (method === 'POST' && path.endsWith('/rollout')) return jsonResponse(fixture)
  if (method === 'POST' && (path.includes('/search') || path.includes('/answer'))) {
    return jsonResponse({ ok: true, answer: '演示操作已完成，已返回本地模拟结果。', results: fixture.items ?? [], items: fixture.items ?? [], total: fixture.total ?? 0 })
  }
  return jsonResponse({ ...body, id: body.id ?? 'demo-' + crypto.randomUUID(), item_id: body.item_id ?? 'demo-' + crypto.randomUUID(), status: body.status ?? 'completed', ok: true, demo: true, created_at: new Date().toISOString() }, method === 'POST' ? 201 : 200)
}

async function mockApi(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = input instanceof Request ? input : undefined
  const url = new URL(request?.url ?? String(input), window.location.origin)
  const path = url.pathname.replace(/^\/api(?=\/)/, '').replace(/\/$/, '') || '/'
  const method = (init?.method ?? request?.method ?? 'GET').toUpperCase()

  if (path.endsWith('/platform/v1/bootstrap')) {
    return jsonResponse({
      auth: { enabled: true },
      version: 'demo-production-ui',
      capabilities: ['prompt_management', 'prompt_evaluation_gate'],
    })
  }

  const collectionPath = Object.keys(demoCollections).find((key) => path === key || path.startsWith(key + '/'))
  if (!collectionPath) return localFallback(input, init, path, method)

  const items = demoCollections[collectionPath]
  const suffix = path.slice(collectionPath.length).replace(/^\//, '')
  if (suffix.includes('/')) return localFallback(input, init, path, method)
  const itemId = suffix ? decodeURIComponent(suffix) : ''
  if (method === 'GET') {
    if (!itemId) return responseForCollection(items, url)
    const item = items.find((entry) => getItemId(entry) === itemId)
    return item
      ? jsonResponse(item)
      : jsonResponse({ error: 'not_found', message: '演示数据不存在' }, 404)
  }

  let body: JsonRecord = {}
  const rawBody = init?.body ?? (request ? await request.clone().text() : undefined)
  if (typeof rawBody === 'string' && rawBody.trim()) {
    try {
      const parsed: unknown = JSON.parse(rawBody)
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed))
        body = parsed as JsonRecord
    } catch {
      return jsonResponse({ error: 'invalid_json', message: '请求体不是有效 JSON' }, 400)
    }
  }

  if (method === 'POST' && !itemId) {
    const idField = collectionPath.endsWith('/workbench/tasks')
      ? 'task_id'
      : collectionPath.endsWith('/approvals')
        ? 'approval_id'
        : collectionPath.endsWith('/runs')
          ? 'run_id'
          : 'item_id'
    const item: JsonRecord = {
      ...body,
      [idField]: body[idField] ?? `demo-${crypto.randomUUID()}`,
      created_at: body.created_at ?? new Date().toISOString(),
    }
    items.unshift(item)
    persistCollections()
    return jsonResponse(item, 201)
  }

  if ((method === 'PUT' || method === 'PATCH') && itemId) {
    const index = items.findIndex((entry) => getItemId(entry) === itemId)
    if (index < 0) return jsonResponse({ error: 'not_found' }, 404)
    items[index] = { ...items[index], ...body, updated_at: new Date().toISOString() }
    persistCollections()
    return jsonResponse(items[index])
  }

  if (method === 'DELETE' && itemId) {
    const index = items.findIndex((entry) => getItemId(entry) === itemId)
    if (index < 0) return jsonResponse({ error: 'not_found' }, 404)
    const [deleted] = items.splice(index, 1)
    persistCollections()
    return jsonResponse({ ok: true, deleted })
  }

  return localFallback(input, init, path, method)
}

// Install the local transport before importing production modules. ApiClient
// captures fetch when its singleton is constructed during module evaluation.
window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) =>
  mockApi(input, init)) as typeof window.fetch

const [{ default: App }, { default: AppProviders }] = await Promise.all([
  import('@/App'),
  import('@/providers/AppProviders'),
])

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AppProviders>
      <App />
    </AppProviders>
  </React.StrictMode>,
)

const demoMarker = document.createElement('div')
demoMarker.textContent = '生产前端组件 · 本地模拟数据'
demoMarker.setAttribute('role', 'note')
demoMarker.style.cssText =
  'position:fixed;right:16px;bottom:12px;z-index:10000;padding:7px 11px;border:1px solid #dbe4f4;border-radius:7px;background:#ffffffed;color:#52627a;font:11px Inter,Arial,sans-serif;box-shadow:0 3px 12px #1d355714;pointer-events:none'
document.body.append(demoMarker)

export {}
