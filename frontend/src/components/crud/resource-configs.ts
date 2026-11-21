import { apiClient } from '@/lib/api/api-client'
import type { CrudConfig, Row } from './types'

const text = (value: unknown): string =>
  typeof value === 'string'
    ? value
    : value === null || value === undefined
      ? ''
      : String(value)

const trimmed = (value: unknown): string => text(value).trim()

const maybe = (value: unknown): string | undefined => {
  const result = trimmed(value)
  return result === '' ? undefined : result
}

const idOf = (row: Record<string, unknown>, key: string): string =>
  encodeURIComponent(text(row[key]))

export const memoryConfig: CrudConfig = {
  title: '记忆治理',
  description:
    '记忆候选、确认、修正与归档。数据来自 Memory API，所有写入都会生成新版本。',
  entityLabel: '记忆',
  listPath: '/platform/v1/memories',
  load: async () => {
    const [active, candidates] = await Promise.all([
      apiClient.get<{ items: Row[] }>('/platform/v1/memories'),
      apiClient.get<{ items: Row[] }>('/platform/v1/memories/candidates'),
    ])
    const seen = new Set<string>()
    const items = [
      ...(candidates?.items ?? []),
      ...(active?.items ?? []),
    ].filter((item) => {
      const id = text(item.memory_id)
      if (!id || seen.has(id)) return false
      seen.add(id)
      return true
    })
    return { items }
  },
  idField: 'memory_id',
  columns: [
    'memory_id',
    'scope',
    'scope_id',
    'content',
    'confidence',
    'status',
    'version',
  ],
  fields: [
    {
      key: 'scope',
      label: '作用域',
      type: 'select',
      createOnly: true,
      defaultValue: 'user',
      options: [
        { value: 'user', label: '用户' },
        { value: 'project', label: '项目' },
        { value: 'agent', label: 'Agent' },
        { value: 'tenant', label: '租户' },
      ],
    },
    {
      key: 'scope_id',
      label: '作用域 ID',
      createOnly: true,
      help: '用户作用域留空时默认为当前用户；项目/Agent/租户需填写对应 ID。',
    },
    {
      key: 'content',
      label: '内容',
      type: 'textarea',
      required: true,
      rows: 4,
    },
    { key: 'source_ref', label: '来源引用', required: true },
    {
      key: 'confidence',
      label: '置信度（0-1）',
      type: 'number',
      defaultValue: 0.5,
    },
    {
      key: 'expires_at',
      label: '过期时间（ISO-8601）',
      createOnly: true,
      placeholder: '2026-12-31T00:00:00+08:00',
    },
  ],
  create: {
    label: '新建记忆候选',
    submit: ({ values }) =>
      apiClient.post('/platform/v1/memories/candidates', {
        scope: trimmed(values.scope),
        scope_id: maybe(values.scope_id),
        content: trimmed(values.content),
        source_ref: trimmed(values.source_ref),
        confidence: Number(values.confidence ?? 0.5),
        expires_at: maybe(values.expires_at),
      }),
  },
  update: {
    label: '保存修正',
    submit: ({ values, row }) =>
      apiClient.patch(`/platform/v1/memories/${idOf(row ?? {}, 'memory_id')}`, {
        content: trimmed(values.content),
        source_ref: trimmed(values.source_ref),
        confidence: Number(values.confidence ?? 0.5),
      }),
  },
  remove: {
    confirm: '确认遗忘这条记忆？记录会被删除。',
    run: (row) =>
      apiClient.delete(`/platform/v1/memories/${idOf(row, 'memory_id')}`),
  },
  rowActions: [
    {
      label: '确认',
      visible: (row) => ['candidate', 'conflict'].includes(text(row.status)),
      run: (row) =>
        apiClient.post(
          `/platform/v1/memories/${idOf(row, 'memory_id')}/confirm`,
        ),
    },
    {
      label: '归档',
      visible: (row) => text(row.status) === 'active',
      run: (row) =>
        apiClient.post(
          `/platform/v1/memories/${idOf(row, 'memory_id')}/archive`,
        ),
    },
  ],
  emptyHint: '当前租户还没有记忆记录。',
}

export const conversationConfig: CrudConfig = {
  title: '会话与消息',
  description: '新建、归档、恢复或删除会话。消息记录在会话详情页查看。',
  entityLabel: '会话',
  listPath: '/platform/v1/sessions?include_archived=true',
  queryKey: 'sessions',
  idField: 'session_id',
  columns: [
    'session_id',
    'title',
    'project_id',
    'status',
    'created_at',
    'updated_at',
  ],
  fields: [
    { key: 'title', label: '标题', required: true, placeholder: '会话标题' },
    {
      key: 'project_id',
      label: '项目 ID',
      createOnly: true,
      help: '可选；填写后会话归属于该项目。',
    },
  ],
  create: {
    label: '新建会话',
    submit: ({ values }) =>
      apiClient.post('/platform/v1/sessions', {
        title: trimmed(values.title),
        project_id: maybe(values.project_id),
      }),
  },
  remove: {
    confirm: '确认删除会话？删除后不可恢复。',
    run: (row) =>
      apiClient.delete(`/platform/v1/sessions/${idOf(row, 'session_id')}`),
  },
  rowActions: [
    {
      label: '归档',
      visible: (row) => text(row.status) === 'active',
      run: (row) =>
        apiClient.post(
          `/platform/v1/sessions/${idOf(row, 'session_id')}/archive`,
        ),
    },
    {
      label: '恢复',
      visible: (row) => text(row.status) === 'archived',
      run: (row) =>
        apiClient.post(
          `/platform/v1/sessions/${idOf(row, 'session_id')}/restore`,
        ),
    },
    {
      label: '详情',
      link: (row) =>
        `/operations/conversations/${encodeURIComponent(text(row.session_id))}`,
    },
  ],
  emptyHint: '当前身份还没有会话记录。',
}

export const fileConfig: CrudConfig = {
  title: '文件与产物',
  description: '上传文件、上传新版本或删除文件资产。',
  entityLabel: '文件',
  listPath: '/platform/v1/files',
  idField: 'asset_id',
  columns: [
    'filename',
    'media_type',
    'size_bytes',
    'status',
    'agent_id',
    'owner_id',
    'session_id',
    'version',
    'created_at',
  ],
  columnLabels: {
    filename: '文件名',
    media_type: '类型',
    size_bytes: '大小',
    status: '状态',
    agent_id: '生成 Agent',
    owner_id: '上传者',
    session_id: '关联会话',
    version: '版本',
    created_at: '创建时间',
  },
  fields: [
    { key: 'file', label: '文件', type: 'file', required: true },
    { key: 'session_id', label: '关联会话 ID', createOnly: true },
    { key: 'project_id', label: '关联项目 ID', createOnly: true },
  ],
  create: {
    label: '上传文件',
    submit: async ({ values, file }) => {
      if (!file) throw new Error('请选择要上传的文件')
      const params = new URLSearchParams()
      const sessionId = maybe(values.session_id)
      const projectId = maybe(values.project_id)
      if (sessionId) params.set('session_id', sessionId)
      if (projectId) params.set('project_id', projectId)
      const query = params.toString()
      return apiClient.post(
        `/platform/v1/files${query ? `?${query}` : ''}`,
        file,
        {
          headers: {
            'Content-Type': file.type || 'application/octet-stream',
            'X-Filename': file.name,
          },
          timeoutMs: 120000,
        },
      )
    },
  },
  update: {
    label: '上传新版本',
    submit: async ({ row, file }) => {
      if (!file) throw new Error('请选择新版本文件')
      return apiClient.post(
        `/platform/v1/files/${idOf(row ?? {}, 'asset_id')}/versions`,
        file,
        {
          headers: {
            'Content-Type': file.type || 'application/octet-stream',
            'X-Filename': file.name,
          },
          timeoutMs: 120000,
        },
      )
    },
  },
  remove: {
    confirm: '确认删除该文件？删除后不可恢复。',
    run: (row) =>
      apiClient.delete(`/platform/v1/files/${idOf(row, 'asset_id')}`),
  },
  rowActions: [
    {
      label: '下载',
      run: async (row) => {
        const data = await apiClient.get<{ url?: string }>(
          `/platform/v1/files/${idOf(row, 'asset_id')}/download-url`,
        )
        if (data?.url) window.open(data.url, '_blank', 'noopener')
        return data
      },
    },
    {
      label: '详情',
      link: (row) =>
        `/operations/files/${encodeURIComponent(text(row.asset_id))}`,
    },
  ],
  filters: [
    {
      key: 'media_type',
      label: '文件类型',
      groups: [
        { label: '全部', test: () => true },
        { label: '文本', test: (v) => typeof v === 'string' && (v.startsWith('text/') || v.includes('json') || v.includes('xml')) },
        { label: '图片', test: (v) => typeof v === 'string' && v.startsWith('image/') },
        { label: 'PDF', test: (v) => v === 'application/pdf' },
      ],
    },
  ],
  emptyHint: '当前身份还没有文件资产。',
}

export const approvalConfig: CrudConfig = {
  title: '审批中心',
  description: '发起审批请求，或在列表中直接通过、驳回与撤回。',
  entityLabel: '审批',
  listPath: '/platform/v1/approvals',
  idField: 'approval_id',
  columns: [
    'approval_id',
    'action',
    'resource',
    'risk_level',
    'status',
    'requested_by',
    'expires_at',
  ],
  fields: [
    {
      key: 'action',
      label: '动作',
      required: true,
      placeholder: '例如 deploy.release',
    },
    {
      key: 'resource',
      label: '资源',
      required: true,
      placeholder: '例如 project:demo',
    },
    { key: 'run_id', label: '关联运行 ID', createOnly: true },
    {
      key: 'parameters',
      label: '参数（JSON）',
      type: 'json',
      createOnly: true,
      rows: 6,
      defaultValue: '{}',
    },
  ],
  create: {
    label: '发起审批',
    submit: ({ values }) =>
      apiClient.post('/platform/v1/approvals', {
        action: trimmed(values.action),
        resource: trimmed(values.resource),
        parameters: values.parameters ?? {},
        run_id: maybe(values.run_id),
      }),
  },
  rowActions: [
    {
      label: '通过',
      visible: (row) => text(row.status) === 'pending',
      run: (row) =>
        apiClient.post(
          `/platform/v1/approvals/${idOf(row, 'approval_id')}/decision`,
          { approved: true },
        ),
    },
    {
      label: '驳回',
      visible: (row) => text(row.status) === 'pending',
      run: (row) =>
        apiClient.post(
          `/platform/v1/approvals/${idOf(row, 'approval_id')}/decision`,
          { approved: false },
        ),
    },
    {
      label: '撤回',
      visible: (row) => text(row.status) === 'pending',
      confirm: '确认撤回该审批请求？',
      run: (row) =>
        apiClient.post(
          `/platform/v1/approvals/${idOf(row, 'approval_id')}/revoke`,
        ),
    },
    {
      label: '详情',
      link: (row) =>
        `/operations/approvals/${encodeURIComponent(text(row.approval_id))}`,
    },
  ],
  emptyHint: '当前没有待处理的审批。',
}

export const evaluationDatasetConfig: CrudConfig = {
  title: '评测数据集',
  description: '导入 JSONL 或 CSV 数据集，每次导入都会生成新版本。',
  entityLabel: '数据集',
  listPath: '/platform/v1/observability/evaluations/datasets',
  idField: 'dataset_id',
  columns: ['dataset_id', 'version', 'case_count', 'created_at'],
  fields: [
    {
      key: 'dataset_id',
      label: '数据集 ID',
      createOnly: true,
      help: '留空由服务端自动生成；填写已有 ID 会创建新版本。',
    },
    {
      key: 'format',
      label: '导入格式',
      type: 'select',
      createOnly: true,
      defaultValue: 'jsonl',
      options: [
        { value: 'jsonl', label: 'JSONL' },
        { value: 'csv', label: 'CSV' },
      ],
    },
    {
      key: 'content',
      label: '数据集内容',
      type: 'textarea',
      required: true,
      rows: 10,
      createOnly: true,
      placeholder:
        'JSONL 每行：{"case_id":"...","input":"...","expected_output":"...","rubric":{}}',
    },
  ],
  create: {
    label: '导入数据集',
    submit: ({ values }) =>
      apiClient.post('/platform/v1/observability/evaluations/datasets/import', {
        dataset_id: maybe(values.dataset_id),
        format: trimmed(values.format) || 'jsonl',
        content: text(values.content),
      }),
  },
  rowActions: [
    {
      label: '版本明细',
      link: (row) =>
        `/operations/evaluation/datasets/${encodeURIComponent(text(row.dataset_id))}`,
    },
  ],
  emptyHint: '还没有评测数据集。',
}

export const evaluationRunConfig: CrudConfig = {
  title: '评测运行与样本',
  description: '选择数据集与模型发起评测；运行结果同步返回。',
  entityLabel: '评测运行',
  listPath: '/platform/v1/observability/evaluations',
  idField: 'eval_run_id',
  fields: [
    { key: 'dataset_id', label: '数据集 ID', required: true },
    { key: 'dataset_version', label: '数据集版本', type: 'number' },
    {
      key: 'model_key',
      label: '模型标识',
      required: true,
      placeholder: '例如 gpt-4o-mini',
    },
  ],
  create: {
    label: '发起评测',
    submit: ({ values }) =>
      apiClient.post(
        '/platform/v1/observability/evaluations/run',
        {
          dataset_id: trimmed(values.dataset_id),
          dataset_version:
            values.dataset_version === undefined ||
            values.dataset_version === ''
              ? undefined
              : Number(values.dataset_version),
          model_key: trimmed(values.model_key),
        },
        { timeoutMs: 120000 },
      ),
  },
  rowActions: [
    {
      label: '评分详情',
      link: (row) =>
        `/operations/evaluation/runs/${encodeURIComponent(text(row.eval_run_id))}`,
    },
  ],
  emptyHint: '还没有评测运行记录。',
}

export const promptConfig: CrudConfig = {
  title: 'Prompt 与模板管理',
  description: '为 Prompt 注册新版本模板。激活与灰度仍需通过评测门禁接口完成。',
  entityLabel: 'Prompt 版本',
  listPath: '/platform/v1/prompts/',
  queryKey: 'prompts',
  idField: 'prompt_id',
  columns: ['prompt_id', 'active_version', 'latest_version', 'total_versions'],
  fields: [
    {
      key: 'prompt_id',
      label: '提示词标识',
      required: true,
      createOnly: true,
      placeholder: '例如 support.system',
    },
    {
      key: 'template',
      label: '模板内容',
      type: 'textarea',
      required: true,
      rows: 10,
      placeholder: '支持 {{variable}} 占位符',
    },
  ],
  create: {
    label: '注册新版本',
    submit: ({ values }) =>
      apiClient.post(
        `/platform/v1/prompts/${encodeURIComponent(trimmed(values.prompt_id))}/versions`,
        { template: text(values.template) },
      ),
  },
  update: {
    label: '注册新版本',
    submit: ({ values, row }) =>
      apiClient.post(
        `/platform/v1/prompts/${idOf(row ?? {}, 'prompt_id')}/versions`,
        { template: text(values.template) },
      ),
  },
  emptyHint: '还没有注册任何 Prompt。',
}

export const schedulerJobConfig: CrudConfig = {
  title: '调度任务',
  description: '启停内置定时作业或手动触发一次执行。作业由服务端预置。',
  entityLabel: '调度任务',
  listPath: '/platform/v1/scheduler/jobs',
  idField: 'job_id',
  columns: [
    'job_id',
    'name',
    'status',
    'enabled',
    'interval_seconds',
    'last_run_at',
    'next_run_at',
    'last_result',
  ],
  fields: [
    {
      key: 'job_id',
      label: '作业类型',
      required: true,
      createOnly: true,
      placeholder: 'connector_sync 或 connector_sync:<连接ID>',
      help: '内置作业也可直接传入 job_id，例如 tick、selfcheck、event_sync。',
    },
    { key: 'name', label: '显示名称', createOnly: true },
    {
      key: 'description',
      label: '说明',
      createOnly: true,
      placeholder: '例如：每小时同步飞书知识库',
    },
    {
      key: 'interval_seconds',
      label: '执行间隔（秒，最小 60）',
      type: 'number',
      defaultValue: 3600,
    },
    {
      key: 'enabled',
      label: '启用',
      type: 'boolean',
      placeholder: '启用定时执行',
    },
  ],
  create: {
    label: '新建调度任务',
    submit: ({ values }) =>
      apiClient.post('/platform/v1/scheduler/jobs', {
        job_id: trimmed(values.job_id),
        name: trimmed(values.name),
        description: trimmed(values.description),
        interval_seconds: Number(values.interval_seconds || 3600),
        enabled: values.enabled === undefined ? true : Boolean(values.enabled),
      }),
  },
  update: {
    label: '保存调度配置',
    submit: ({ values, row }) =>
      apiClient.patch(
        `/platform/v1/scheduler/jobs/${idOf(row ?? {}, 'job_id')}`,
        {
          enabled: Boolean(values.enabled),
          interval_seconds: Number(values.interval_seconds || 3600),
        },
      ),
  },
  rowActions: [
    {
      label: '立即执行',
      confirm: '确认立即执行一次该作业？',
      run: (row) =>
        apiClient.post(
          `/platform/v1/scheduler/jobs/${idOf(row, 'job_id')}/run`,
        ),
    },
  ],
  emptyHint: '服务端没有返回内置调度作业。',
}

export const subscriptionConfig: CrudConfig = {
  title: '事件订阅',
  description:
    '配置事件通知的接收人。目标格式为 open_id:ou_xxx 或 chat_id:oc_xxx。',
  entityLabel: '事件订阅',
  listPath: '/platform/v1/scheduler/subscriptions',
  idField: 'subscription_id',
  columns: [
    'subscription_id',
    'event_type',
    'channel',
    'target',
    'enabled',
    'created_by',
    'created_at',
  ],
  fields: [
    {
      key: 'event_type',
      label: '事件类型',
      type: 'select',
      required: true,
      createOnly: true,
      defaultValue: 'task.failed',
      options: [
        { value: 'task.failed', label: '任务失败' },
        { value: 'run.failed', label: '运行失败' },
        { value: 'approval.pending', label: '审批待处理' },
        { value: 'daily', label: '每日汇总' },
      ],
    },
    {
      key: 'target',
      label: '接收目标',
      required: true,
      placeholder: 'open_id:ou_xxx 或 chat_id:oc_xxx',
    },
    { key: 'enabled', label: '启用', type: 'boolean', placeholder: '启用订阅' },
  ],
  create: {
    label: '新建订阅',
    submit: ({ values }) =>
      apiClient.post('/platform/v1/scheduler/subscriptions', {
        event_type: trimmed(values.event_type),
        target: trimmed(values.target),
        enabled: values.enabled === undefined ? true : Boolean(values.enabled),
      }),
  },
  update: {
    label: '保存订阅',
    submit: ({ values, row }) =>
      apiClient.patch(
        `/platform/v1/scheduler/subscriptions/${idOf(row ?? {}, 'subscription_id')}`,
        {
          target: trimmed(values.target),
          enabled: Boolean(values.enabled),
        },
      ),
  },
  remove: {
    confirm: '确认删除该事件订阅？',
    run: (row) =>
      apiClient.delete(
        `/platform/v1/scheduler/subscriptions/${idOf(row, 'subscription_id')}`,
      ),
  },
  emptyHint: '还没有事件订阅。',
}

export const connectorConfig: CrudConfig = {
  title: '数据连接器',
  description:
    '接入 PostgreSQL / MySQL / S3 / 飞书 / Notion / 通用 HTTP 数据源。凭据写入本地密钥库，列表只展示引用。',
  entityLabel: '连接器',
  listPath: '/platform/v1/connectors',
  idField: 'connection_id',
  columns: [
    'connection_id',
    'provider',
    'display_name',
    'status',
    'last_synced_at',
    'last_error',
  ],
  fields: [
    {
      key: 'connection_id',
      label: '连接 ID',
      required: true,
      createOnly: true,
      placeholder: '例如 feishu-wiki',
    },
    {
      key: 'provider',
      label: '数据源类型',
      type: 'select',
      required: true,
      createOnly: true,
      defaultValue: 'postgres',
      options: [
        { value: 'postgres', label: 'PostgreSQL' },
        { value: 'mysql', label: 'MySQL' },
        { value: 's3', label: 'S3 / 对象存储' },
        { value: 'feishu', label: '飞书' },
        { value: 'notion', label: 'Notion' },
        { value: 'http', label: '通用 HTTP JSON' },
      ],
    },
    { key: 'display_name', label: '显示名称', createOnly: true },
    {
      key: 'scopes',
      label: '同步范围',
      type: 'textarea',
      required: true,
      rows: 3,
      createOnly: true,
      placeholder: 'table:public.users\nquery:SELECT id,name FROM orders',
      help: '每行一个。支持 table: / query:（SQL）、folder: / bitable: / doc:（飞书）、database: / page:（Notion）、前缀或 path: / url:（S3 与 HTTP）。',
    },
    {
      key: 'credential',
      label: '凭据（JSON，写入本地密钥库）',
      type: 'json',
      rows: 6,
      defaultValue: '',
      help: '例如 {"dsn":"postgresql://user:pass@host/db"} 或 {"app_id":"...","app_secret":"..."}。留空则需填写 secret_ref。',
    },
    {
      key: 'secret_ref',
      label: '已有密钥引用',
      createOnly: true,
      placeholder: 'secret://fde/connector/...',
    },
  ],
  create: {
    label: '新建连接器',
    submit: ({ values }) => {
      const scopes = text(values.scopes)
        .split(/[\n,]/)
        .map((item) => item.trim())
        .filter(Boolean)
      const credential = values.credential
      const hasCredential =
        credential &&
        typeof credential === 'object' &&
        Object.keys(credential as Record<string, unknown>).length > 0
      return apiClient.post('/platform/v1/connectors', {
        connection_id: trimmed(values.connection_id),
        provider: trimmed(values.provider),
        display_name: trimmed(values.display_name) || trimmed(values.connection_id),
        scopes,
        secret_ref: maybe(values.secret_ref),
        credential: hasCredential ? credential : undefined,
      })
    },
  },
  update: {
    label: '轮换凭据',
    submit: ({ values, row }) => {
      const credential = values.credential
      if (
        !credential ||
        typeof credential !== 'object' ||
        Object.keys(credential as Record<string, unknown>).length === 0
      ) {
        throw new Error('请填写新的凭据 JSON')
      }
      return apiClient.post(
        `/platform/v1/connectors/${idOf(row ?? {}, 'connection_id')}/credential`,
        { credential },
      )
    },
  },
  rowActions: [
    {
      label: '立即同步',
      confirm: '确认立即同步一次该连接器？',
      run: (row) =>
        apiClient.post(
          `/platform/v1/connectors/${idOf(row, 'connection_id')}/sync`,
          {},
          { timeoutMs: 120000 },
        ),
    },
    {
      label: '撤销',
      confirm: '确认撤销该连接器？撤销后将停止同步，数据保留。',
      visible: (row) => text(row.status) === 'active',
      run: (row) =>
        apiClient.post(
          `/platform/v1/connectors/${idOf(row, 'connection_id')}/revoke?disposition=retain`,
        ),
    },
  ],
  emptyHint: '还没有数据连接器。',
}

export const webhookConfig: CrudConfig = {
  title: 'Webhook 订阅',
  description:
    '把平台事件推送到外部 HTTPS 端点。签名密钥保存在本地密钥库，支持重试与死信。',
  entityLabel: 'Webhook 订阅',
  listPath: '/platform/v1/webhooks',
  idField: 'subscription_id',
  columns: [
    'subscription_id',
    'url',
    'topics',
    'status',
    'created_at',
  ],
  fields: [
    {
      key: 'url',
      label: '目标地址（HTTPS）',
      required: true,
      createOnly: true,
      placeholder: 'https://example.com/hooks/fde',
      help: '必须是公网 HTTPS 地址，不能指向 localhost 或内网。',
    },
    {
      key: 'topics',
      label: '订阅事件（逗号分隔）',
      required: true,
      createOnly: true,
      placeholder: 'approval.pending, run.failed',
    },
    {
      key: 'signing_secret',
      label: '签名密钥（至少 32 个字符）',
      type: 'textarea',
      required: true,
      rows: 2,
      placeholder: '用于 X-Platform-Signature 的 HMAC 密钥',
    },
  ],
  create: {
    label: '新建 Webhook',
    submit: ({ values }) => {
      const topics = text(values.topics)
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean)
      return apiClient.post('/platform/v1/webhooks', {
        url: trimmed(values.url),
        topics,
        signing_secret: text(values.signing_secret),
      })
    },
  },
  update: {
    label: '轮换签名密钥',
    submit: ({ values, row }) =>
      apiClient.post(
        `/platform/v1/webhooks/${idOf(row ?? {}, 'subscription_id')}/rotate`,
        { signing_secret: text(values.signing_secret) },
      ),
  },
  rowActions: [
    {
      label: '停用',
      confirm: '确认停用该 Webhook 订阅？后续事件将不再推送。',
      visible: (row) => text(row.status) === 'active',
      run: (row) =>
        apiClient.post(
          `/platform/v1/webhooks/${idOf(row, 'subscription_id')}/disable`,
        ),
    },
  ],
  emptyHint: '还没有 Webhook 订阅。',
}
