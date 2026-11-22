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
  if (!collectionPath) {
    return jsonResponse({
      error: 'demo_endpoint_not_implemented',
      message: `演示环境尚未接入接口：${method} ${path}`,
    }, 501)
  }

  const items = demoCollections[collectionPath]
  const suffix = path.slice(collectionPath.length).replace(/^\//, '')
  if (suffix.includes('/')) {
    return jsonResponse({
      error: 'demo_subresource_not_implemented',
      message: `演示环境尚未接入子资源接口：${method} ${path}`,
    }, 501)
  }
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

  return jsonResponse({
    error: 'demo_operation_not_supported',
    message: `演示环境不支持此操作：${method} ${path}`,
  }, 501)
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
