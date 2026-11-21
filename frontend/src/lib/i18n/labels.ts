/** 服务端字段与枚举值的中文展示映射（仅用于 UI，不改变数据）。 */

const FIELD_LABELS: Record<string, string> = {
  // 通用
  id: 'ID',
  name: '名称',
  title: '标题',
  description: '描述',
  status: '状态',
  kind: '类型',
  type: '类型',
  action: '动作',
  resource: '资源',
  reason: '原因',
  detail: '详情',
  summary: '摘要',
  content: '内容',
  metadata: '元数据',
  tags: '标签',
  priority: '优先级',
  scope_type: '范围类型',
  scope_id: '范围',
  period_start: '周期开始',
  period_end: '周期结束',
  hard_tokens: '硬性 Token 上限',
  hard_cost_usd: '硬性费用上限（美元）',
  hard_concurrency: '并发上限',
  soft_tokens: '软性 Token 上限',
  soft_cost_usd: '软性费用上限（美元）',
  table_name: '数据表',
  row_count: '记录数',
  version: '版本',
  active_version: '生效版本',
  latest_version: '最新版本',
  item_id: '条目 ID',
  definition: '定义',
  published_at: '发布时间',
  created_at: '创建时间',
  updated_at: '更新时间',
  deleted_at: '删除时间',
  occurred_at: '发生时间',
  started_at: '开始时间',
  finished_at: '结束时间',
  expires_at: '过期时间',
  decided_at: '决定时间',
  joined_at: '加入时间',
  last_login_at: '最近登录',
  created_by: '创建人',
  updated_by: '更新人',
  requested_by: '申请人',
  decided_by: '审批人',
  actor_id: '操作者',
  subject_id: '对象 ID',
  subject_type: '对象类型',
  source: '来源',
  outcome: '结果',
  // 身份与租户
  tenant_id: '租户 ID',
  user_id: '用户 ID',
  email: '邮箱',
  display_name: '显示名称',
  role_id: '角色 ID',
  role_name: '角色',
  subject: '主体',
  member_name: '成员',
  roles: '角色',
  permission_key: '权限键',
  is_system: '内置角色',
  resource_type: '资源类型',
  resource_id: '资源 ID',
  revoked_at: '撤销时间',
  tenant_name: '租户名称',
  ip_address: 'IP 地址',
  user_agent: '客户端',
  request_id: '请求 ID',
  // 任务 / 运行
  task_id: '任务 ID',
  run_id: '运行 ID',
  agent_id: 'Agent ID',
  workflow_id: '工作流 ID',
  trace_id: 'Trace ID',
  input: '输入',
  output: '输出',
  error: '错误',
  error_code: '错误码',
  last_error: '最近错误',
  attempts: '尝试次数',
  max_attempts: '最大尝试次数',
  duration_ms: '耗时（毫秒）',
  token_count: 'Token 数',
  latency_ms: '延迟（毫秒）',
  cost_usd: '成本（美元）',
  model: '模型',
  model_key: '模型',
  model_template_ref: '模型模板',
  model_template_id: '模型模板',
  model_provider_id: '模型供应商',
  provider: '供应商',
  base_url: '接口地址',
  api_key_masked: 'API Key（脱敏）',
  template_id: '模板 ID',
  policy_id: '策略 ID',
  tool_id: '工具 ID',
  skill_id: 'Skill ID',
  tool_refs: '工具引用',
  skill_refs: 'Skill 引用',
  workflow_refs: '工作流引用',
  instructions: '指令',
  nodes: '节点',
  edges: '连线',
  // 审批
  approval_id: '审批 ID',
  risk_level: '风险等级',
  approvals: '审批',
  pending: '待处理',
  // 会话 / 记忆 / 文件 / 知识
  session_id: '会话',
  session_label: '会话',
  conversation_id: '会话 ID',
  memory_id: '记忆 ID',
  scope: '范围',
  importance: '重要度',
  source_refs: '来源引用',
  messages: '消息',
  role: '角色',
  asset_id: '文件 ID',
  file_id: '文件 ID',
  filename: '文件名',
  media_type: '文件类型',
  mime_type: '文件类型',
  size_bytes: '文件大小',
  storage_key: '存储标识',
  checksum: '校验和',
  sha256: 'SHA-256',
  owner_id: '上传者',
  agent_id: '生成 Agent',
  collection_id: '知识库 ID',
  document_id: '文档 ID',
  chunk_id: '分块 ID',
  ordinal: '序号',
  text: '文本',
  page_number: '页码',
  content_hash: '内容哈希',
  embedding_status: '向量化状态',
  embedding_model: '向量模型',
  dimensions: '维度',
  index_id: '索引 ID',
  index_type: '索引类型',
  source_uri: '来源地址',
  // 评测
  dataset_id: '数据集 ID',
  evaluation_run_id: '评测运行 ID',
  score: '评分',
  metric: '指标',
  passed: '是否通过',
  // 审计
  audit_id: '审计 ID',
  event_id: '事件 ID',
  sequence: '序号',
  // 调度 / 配额
  job_id: '任务 ID',
  schedule: '调度表达式',
  next_run_at: '下次运行',
  quota_key: '配额键',
  limit: '上限',
  used: '已用',
  remaining: '剩余',
  period: '周期',
  amount: '数量',
  currency: '币种',
  // 表格 / 包装
  items: '条目',
  rows: '行',
  total: '总数',
  count: '数量',
  section: '分区',
  // 指标
  help: '说明',
  labels: '标签',
  value: '数值',
}

const VALUE_LABELS: Record<string, string> = {
  // 指标类型
  gauge: '瞬时值',
  counter: '累计值',
  histogram: '直方图',
  summary: '摘要',
  // 运行 / 任务
  pending: '待处理',
  queued: '排队中',
  running: '运行中',
  executing: '执行中',
  waiting_approval: '等待审批',
  waiting_human: '等待人工',
  completed: '已完成',
  succeeded: '成功',
  success: '成功',
  done: '已完成',
  failed: '失败',
  failure: '失败',
  error: '错误',
  cancelled: '已取消',
  canceled: '已取消',
  cancelling: '取消中',
  paused: '已暂停',
  blocked: '已阻塞',
  timeout: '超时',
  retrying: '重试中',
  // 审批
  approved: '已通过',
  rejected: '已拒绝',
  expired: '已过期',
  // 注册表
  published: '已发布',
  draft: '草稿',
  disabled: '已停用',
  archived: '已归档',
  active: '启用',
  inactive: '停用',
  // Agent 状态
  idle: '空闲',
  busy: '忙碌',
  offline: '离线',
  online: '在线',
  degraded: '降级',
  healthy: '健康',
  ok: '正常',
  unknown: '未知',
  ready: '就绪',
  // 角色 / 主体
  user: '用户',
  service_account: '服务账号',
  system: '系统',
  owner: '所有者',
  admin: '管理员',
  member: '成员',
  viewer: '只读成员',
  // 文件状态
  available: '可用',
  deleted: '已删除',
  uploading: '上传中',
  corrupted: '已损坏',
  // 配额范围
  tenant: '租户',
  project: '项目',
  queue: '队列',
  worker_pool: '工人池',
  // 知识图谱
  document: '文档',
  memory: '记忆',
  blackboard_entry: '黑板条目',
  concept: '概念',
  person: '人物',
  organization: '组织',
  topic: '主题',
  references: '引用',
  member_of: '属于项目',
  mentions: '提及',
  related_to: '相关',
  // 类型
  agent: 'Agent',
  task: '任务',
  run: '运行',
  workflow: '工作流',
  tool: '工具',
  skill: 'Skill',
  approval: '审批',
  dead_letter: '死信',
  model_provider: '模型供应商',
  model_template: '模型模板',
  routing_policy: '路由策略',
  // 优先级
  urgent: '紧急',
  high: '高',
  normal: '普通',
  low: '低',
  // 协作黑板条目
  fact: '事实',
  claim: '主张',
  evidence: '证据',
  challenge: '质疑',
  conflict: '冲突',
  decision: '决策',
  artifact: '交付物',
  note: '备注',
  // 向量化
  indexed: '已索引',
  processing: '处理中',
  not_indexed: '未索引',
  // 审计结果
  allow: '允许',
  deny: '拒绝',
  granted: '已授权',
  revoked: '已撤销',
  // 角色名称
  'tenant admin': '租户管理员',
  // 权限键
  'tenant.manage': '管理租户',
  'user.manage': '管理成员',
  'identity.role.manage': '管理角色',
  'agent.run': '运行 Agent',
  'agent.manage': '管理 Agent',
  'tool.execute': '执行工具',
  'knowledge.read': '查看知识库',
  'knowledge.manage': '管理知识库',
  'project.read': '查看项目',
  'project.write': '编辑项目',
  'audit.read': '查看审计',
}

const EVENT_LABELS: Record<string, string> = {
  'run.started': '运行开始',
  'run.succeeded': '运行成功',
  'run.failed': '运行失败',
  'run.cancelled': '运行取消',
  'task.created': '任务创建',
  'task.updated': '任务更新',
  'plan.created': '计划创建',
  'plan.updated': '计划更新',
  'message.created': '消息创建',
  'tool.called': '工具调用',
  'tool.result': '工具返回',
  'tool.failed': '工具失败',
  'model.call': '模型调用',
  'model.result': '模型返回',
  'approval.requested': '发起审批',
  'approval.decided': '审批完成',
  'agent.step': 'Agent 步骤',
  handoff: '任务移交',
}

export const DATE_KEYS = new Set([
  'created_at',
  'updated_at',
  'deleted_at',
  'occurred_at',
  'started_at',
  'finished_at',
  'expires_at',
  'decided_at',
  'joined_at',
  'last_login_at',
  'published_at',
  'next_run_at',
  'revoked_at',
])

export function fieldLabel(key: string): string {
  return FIELD_LABELS[key] ?? FIELD_LABELS[key.toLowerCase()] ?? key
}

export function valueLabel(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'number') return String(value)
  if (typeof value === 'string') {
    const mapped = VALUE_LABELS[value.toLowerCase()]
    if (mapped) return mapped
    return value
  }
  return String(value)
}

export function eventLabel(eventType: unknown): string {
  if (typeof eventType !== 'string') return valueLabel(eventType)
  return EVENT_LABELS[eventType] ?? EVENT_LABELS[eventType.toLowerCase()] ?? eventType
}

export function formatDateTime(value: unknown): string {
  if (!value) return '—'
  const date = new Date(String(value))
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString('zh-CN', { hour12: false })
}

/** 表格/详情单元格：按字段决定时间、枚举或原值的中文展示。 */
export function displayValue(key: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (DATE_KEYS.has(key) || key.endsWith('_at')) return formatDateTime(value)
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'string' || typeof value === 'number') {
    return valueLabel(value)
  }
  return localizedJson(value)
}

/** 把 JSON 的字段名与枚举值翻译成中文后再格式化。 */
export function localizedJson(value: unknown): string {
  return JSON.stringify(translate(value), null, 2)
}

function translate(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(translate)
  if (value && typeof value === 'object') {
    const result: Record<string, unknown> = {}
    for (const [rawKey, rawValue] of Object.entries(
      value as Record<string, unknown>,
    )) {
      const label = fieldLabel(rawKey)
      let next = rawValue
      if (rawKey === 'status' || rawKey.endsWith('_status')) {
        next = valueLabel(rawValue)
      } else if (typeof next === 'string' && VALUE_LABELS[next.toLowerCase()]) {
        next = VALUE_LABELS[next.toLowerCase()]
      } else {
        next = translate(next)
      }
      // 避免不同英文字段映射到同一中文键时互相覆盖
      result[label in result ? `${label}（${rawKey}）` : label] = next
    }
    return result
  }
  return value
}
