export interface PageRoute {
  path: string
  pageId: string
  title: string
  section: string
  description: string
  parentPath?: string
  authOnly?: boolean
  capability?: string
  permission?: string
  entityIdParam?: string
  owner: string
}

export interface NavigationGroup {
  id: 'workspace' | 'operations' | 'administration' | 'global'
  title: string
  items: PageRoute[]
}

export const pageRoutes: PageRoute[] = [
  {
    path: '/workspace/overview',
    pageId: 'WS-OVERVIEW',
    title: '运营驾驶舱',
    section: '工作空间',
    description:
      '平台运行概况与业务入口。当前仅展示导航框架，不填充未经 API 证实的指标。',
    owner: '#744',
  },
  {
    path: '/workspace/quickstart',
    pageId: 'WS-QUICKSTART',
    title: '快速使用',
    section: '工作空间',
    description: '选择 Agent、填写内容并提交任务的最短路径。',
    owner: '#744',
  },
  {
    path: '/workspace/status',
    pageId: 'WS-STATUS',
    title: '运行看板',
    section: '工作空间',
    description: '服务状态与任务进展的一屏总览。',
    owner: '#744',
  },
  {
    path: '/workspace/tasks',
    pageId: 'WS-TASKS',
    title: '任务指挥中心',
    section: '工作空间',
    description: '任务队列与执行状态入口。',
    owner: '#745',
  },
  {
    path: '/workspace/collaboration',
    pageId: 'WS-COLLAB',
    title: '智能体协作中心',
    section: '工作空间',
    description: '智能体、工作流运行与协作态势入口。',
    owner: '#746',
  },
  {
    path: '/workspace/quality-cost',
    pageId: 'WS-COST',
    title: '成本与质量中心',
    section: '工作空间',
    description: '成本、质量与评测聚合入口。',
    owner: '#747',
  },
  {
    path: '/workspace/office',
    pageId: 'WS-OFFICE',
    title: '3D 数字办公室',
    section: '工作空间',
    description: '数字办公室入口；真实场景与状态由 Office 模块交付。',
    owner: '#748',
  },
  {
    path: '/operations/agents',
    pageId: 'OPS-AGENTS',
    title: '智能体运行态',
    section: '智能体运营',
    description: '智能体运行状态与负载入口。',
    owner: '#746',
  },
  {
    path: '/operations/agents/:agentId',
    pageId: 'OPS-AGENT-DETAIL',
    title: '智能体运行详情',
    section: '智能体运营',
    description: '智能体运行态唯一详情路由。',
    parentPath: '/operations/agents',
    entityIdParam: 'agentId',
    owner: '#746',
  },
  {
    path: '/operations/tasks/:taskId',
    pageId: 'WS-TASK-DETAIL',
    title: '任务详情',
    section: '智能体运营',
    description: '任务实体唯一详情路由。',
    parentPath: '/workspace/tasks',
    entityIdParam: 'taskId',
    owner: '#745',
  },
  {
    path: '/operations/runs/:runId',
    pageId: 'WS-RUN-DETAIL',
    title: '运行链路详情',
    section: '智能体运营',
    description: '运行实体唯一详情路由。',
    parentPath: '/workspace/tasks',
    entityIdParam: 'runId',
    owner: '#745',
  },
  {
    path: '/operations/workflow-runs/:runId',
    pageId: 'WS-WORKFLOW-RUN',
    title: '工作流运行详情',
    section: '智能体运营',
    description: '工作流运行实例详情。',
    parentPath: '/workspace/collaboration',
    entityIdParam: 'runId',
    owner: '#746',
  },
  {
    path: '/operations/approvals',
    pageId: 'OPS-APPROVALS',
    title: '审批中心',
    section: '智能体运营',
    description: '待处理审批入口。',
    owner: '#750',
  },
  {
    path: '/operations/approvals/:approvalId',
    pageId: 'OPS-APPROVAL-DETAIL',
    title: '审批详情',
    section: '智能体运营',
    description: '审批实体唯一详情路由。',
    parentPath: '/operations/approvals',
    entityIdParam: 'approvalId',
    owner: '#750',
  },
  {
    path: '/operations/knowledge-bases',
    pageId: 'OPS-KNOWLEDGE',
    title: '知识库',
    section: '智能体运营',
    description: '知识集合、文档、分块与检索。',
    owner: '#760',
  },
  {
    path: '/operations/knowledge-graph',
    pageId: 'OPS-KNOWLEDGE-GRAPH',
    title: '知识图谱',
    section: '智能体运营',
    description: '实体检索、邻接关系与图谱质量指标。',
    owner: '#760',
  },
  {
    path: '/operations/rag-playground',
    pageId: 'OPS-RAG-PLAYGROUND',
    title: 'RAG 实验台',
    section: '智能体运营',
    description: '检索问答实验与评测集指标计算。',
    owner: '#760',
  },
  {
    path: '/operations/batches',
    pageId: 'OPS-BATCHES',
    title: '批量执行',
    section: '智能体运营',
    description: '按模板批量提交数据集，查看进度并取消或重试。',
    owner: '#745',
  },
  {
    path: '/operations/replay',
    pageId: 'OPS-REPLAY',
    title: '运行回放',
    section: '智能体运营',
    description: '读取持久化上下文快照与 trace 时间线，单步回放 Run 执行过程。',
    owner: '#745',
  },
  {
    path: '/operations/alerts',
    pageId: 'OPS-ALERTS',
    title: '告警中心',
    section: '智能体运营',
    description: '查看未关闭告警并确认或标记解决。',
    owner: '#751',
  },
  {
    path: '/operations/documents/:documentId',
    pageId: 'OPS-DOCUMENT',
    title: '文档详情',
    section: '智能体运营',
    description: '文档实体唯一详情路由。',
    parentPath: '/operations/knowledge-bases',
    entityIdParam: 'documentId',
    owner: '#760',
  },
  {
    path: '/operations/memories',
    pageId: 'OPS-MEMORY',
    title: '记忆治理',
    section: '智能体运营',
    description: '记忆治理入口。',
    owner: '#750',
  },
  {
    path: '/operations/conversations',
    pageId: 'OPS-CONVERSATIONS',
    title: '会话与消息',
    section: '智能体运营',
    description: '会话与消息记录入口。',
    owner: '#750',
  },
  {
    path: '/operations/files',
    pageId: 'OPS-FILES',
    title: '文件与产物',
    section: '智能体运营',
    description: '文件和运行产物入口。',
    owner: '#750',
  },
  {
    path: '/admin/agents',
    pageId: 'ADM-AGENTS',
    title: '智能体定义',
    section: '平台管理',
    description:
      'Agent 定义 CRUD、指令版本、模型模板与工具/Skill/Workflow 关联。',
    owner: '#757',
  },
  {
    path: '/admin/workflows',
    pageId: 'ADM-WORKFLOWS',
    title: '工作流设计',
    section: '平台管理',
    description: '工作流定义列表与可视化设计器入口。',
    owner: '#758',
  },
  {
    path: '/admin/models/providers',
    pageId: 'ADM-MODEL-PROVIDERS',
    title: '模型供应商',
    section: '平台管理',
    description: '模型 Provider 配置入口。',
    owner: '#749',
  },
  {
    path: '/admin/models/templates',
    pageId: 'ADM-MODEL-TEMPLATES',
    title: '模型模板与目录',
    section: '平台管理',
    description: '模型模板与目录配置入口。',
    owner: '#749',
  },
  {
    path: '/admin/models/routing',
    pageId: 'ADM-MODEL-ROUTING',
    title: '模型路由策略',
    section: '平台管理',
    description: '模型路由策略配置入口。',
    owner: '#749',
  },
  {
    path: '/admin/tools',
    pageId: 'ADM-TOOLS',
    title: '工具与 MCP',
    section: '平台管理',
    description: '工具和 MCP Server 管理入口。',
    owner: '#749',
  },
  {
    path: '/admin/skills',
    pageId: 'ADM-SKILLS',
    title: '技能包',
    section: '平台管理',
    description: '技能包管理入口。',
    owner: '#749',
  },
  {
    path: '/admin/organization',
    pageId: 'ADM-ORG',
    title: '组织与成员',
    section: '平台管理',
    description: '组织、部门、项目与成员管理；仅权限模式且具备对应读取权限时可用。',
    owner: '#749',
    authOnly: true,
    permission: 'platform.organization.read',
  },
  {
    path: '/admin/access-control',
    pageId: 'ADM-RBAC',
    title: '角色与权限',
    section: '平台管理',
    description: '角色与权限管理；仅权限模式且具备对应读取权限时可用。',
    owner: '#749',
    authOnly: true,
    permission: 'platform.rbac.read',
  },
  {
    path: '/admin/extensions',
    pageId: 'ADM-EXTENSIONS',
    title: '扩展发布者',
    section: '平台管理',
    description: '登记或轮换发布者公钥，撤销已授权密钥。',
    owner: '#749',
  },
  {
    path: '/admin/connectors',
    pageId: 'ADM-CONNECTORS',
    title: '数据连接器',
    section: '平台管理',
    description: '配置 PostgreSQL / MySQL / S3 / 飞书 / Notion / HTTP 数据源并执行同步。',
    owner: '#751',
  },
  {
    path: '/admin/webhooks',
    pageId: 'ADM-WEBHOOKS',
    title: 'Webhook 订阅',
    section: '平台管理',
    description: '订阅平台事件并推送到外部 HTTPS 端点，支持签名、重试与死信。',
    owner: '#751',
  },
  {
    path: '/admin/settings',
    pageId: 'SYS-SETTINGS',
    title: '系统设置',
    section: '平台管理',
    description: '配置配额、预算上限与并发限制，并查看存储统计。',
    owner: '#751',
  },
  {
    path: '/operations/blackboard/:entryId',
    pageId: 'WS-BLACKBOARD',
    title: '协作黑板条目详情',
    section: '智能体运营',
    description: '协作黑板条目唯一详情路由。',
    parentPath: '/workspace/collaboration',
    entityIdParam: 'entryId',
    owner: '#746',
  },
  {
    path: '/admin/prompts',
    pageId: 'ADM-PROMPTS',
    title: '提示词与模板管理',
    section: '平台管理',
    description: '提示词注册条件能力入口。',
    owner: '#749',
    capability: 'prompt_management',
  },
  {
    path: '/operations/evaluation/datasets',
    pageId: 'OPS-EVAL-DATASETS',
    title: '评测数据集',
    section: '智能体运营',
    description: '评测数据集列表入口。',
    owner: '#750',
  },
  {
    path: '/operations/evaluation/runs',
    pageId: 'OPS-EVAL-RUNS',
    title: '评测运行与样本',
    section: '智能体运营',
    description: '评测运行列表入口。',
    owner: '#750',
  },
  {
    path: '/operations/evaluation/insights',
    pageId: 'OPS-EVAL-INSIGHTS',
    title: '评测洞察',
    section: '智能体运营',
    description: '运行对比、灰度发布、评分与用例分析。',
    owner: '#750',
  },
]

export const detailRoutes: PageRoute[] = [
  {
    path: '/operations/knowledge-bases/:collectionId',
    pageId: 'OPS-KNOWLEDGE-COLLECTION-DETAIL',
    title: '知识集合详情',
    section: '智能体运营',
    description: '集合成员文档与索引版本。',
    parentPath: '/operations/knowledge-bases',
    entityIdParam: 'collectionId',
    owner: '#760',
  },
  {
    path: '/operations/evaluation/datasets/:datasetId',
    pageId: 'OPS-EVAL-DATASETS',
    title: '评测数据集详情',
    section: '智能体运营',
    description: '评测数据集唯一详情路由。',
    parentPath: '/operations/evaluation/datasets',
    entityIdParam: 'datasetId',
    owner: '#750',
  },
  {
    path: '/operations/evaluation/runs/:evaluationRunId',
    pageId: 'OPS-EVAL-RUNS',
    title: '评测运行详情',
    section: '智能体运营',
    description: '评测运行唯一详情路由。',
    parentPath: '/operations/evaluation/runs',
    entityIdParam: 'evaluationRunId',
    owner: '#750',
  },
  {
    path: '/admin/models/providers/:providerId',
    pageId: 'ADM-MODEL-PROVIDERS',
    title: '模型供应商详情',
    section: '平台管理',
    description: '模型供应商唯一详情路由。',
    parentPath: '/admin/models/providers',
    entityIdParam: 'providerId',
    owner: '#749',
  },
  {
    path: '/admin/models/templates/:templateId',
    pageId: 'ADM-MODEL-TEMPLATES',
    title: '模型模板详情',
    section: '平台管理',
    description: '模型模板唯一详情路由。',
    parentPath: '/admin/models/templates',
    entityIdParam: 'templateId',
    owner: '#749',
  },
  {
    path: '/admin/models/routing/:policyId',
    pageId: 'ADM-MODEL-ROUTING',
    title: '模型路由策略详情',
    section: '平台管理',
    description: '路由策略唯一详情路由。',
    parentPath: '/admin/models/routing',
    entityIdParam: 'policyId',
    owner: '#749',
  },
  {
    path: '/admin/tools/:toolId',
    pageId: 'ADM-TOOLS',
    title: '工具详情',
    section: '平台管理',
    description: '工具/MCP Server 唯一详情路由。',
    parentPath: '/admin/tools',
    entityIdParam: 'toolId',
    owner: '#749',
  },
  {
    path: '/admin/skills/:skillId',
    pageId: 'ADM-SKILLS',
    title: '技能包详情',
    section: '平台管理',
    description: '技能包唯一详情路由。',
    parentPath: '/admin/skills',
    entityIdParam: 'skillId',
    owner: '#749',
  },
  {
    path: '/admin/agents/:agentId',
    pageId: 'ADM-AGENT-DETAIL',
    title: '智能体定义详情',
    section: '平台管理',
    description: '智能体定义详情、配置与版本历史。',
    parentPath: '/admin/agents',
    entityIdParam: 'agentId',
    owner: '#757',
  },
  {
    path: '/admin/workflows/:workflowId/edit',
    pageId: 'ADM-WORKFLOW-EDITOR',
    title: '工作流设计器',
    section: '平台管理',
    description: '工作流定义可视化编辑路由。',
    parentPath: '/admin/workflows',
    entityIdParam: 'workflowId',
    owner: '#758',
  },
  {
    path: '/admin/workflows/:workflowId',
    pageId: 'ADM-WORKFLOW-DETAIL',
    title: '工作流定义详情',
    section: '平台管理',
    description: '工作流定义详情与版本入口。',
    parentPath: '/admin/workflows',
    entityIdParam: 'workflowId',
    owner: '#758',
  },
  {
    path: '/operations/memories/:memoryId',
    pageId: 'OPS-MEMORY',
    title: '记忆详情',
    section: '智能体运营',
    description: '记忆实体唯一详情路由。',
    parentPath: '/operations/memories',
    entityIdParam: 'memoryId',
    owner: '#750',
  },
  {
    path: '/operations/conversations/:conversationId',
    pageId: 'OPS-CONVERSATIONS',
    title: '会话详情',
    section: '智能体运营',
    description: '会话实体唯一详情路由。',
    parentPath: '/operations/conversations',
    entityIdParam: 'conversationId',
    owner: '#750',
  },
  {
    path: '/operations/files/:fileId',
    pageId: 'OPS-FILES',
    title: '文件与产物详情',
    section: '智能体运营',
    description: '文件/产物实体唯一详情路由。',
    parentPath: '/operations/files',
    entityIdParam: 'fileId',
    owner: '#750',
  },
  {
    path: '/admin/audit/:eventId',
    pageId: 'GLOBAL-AUDIT',
    title: '审计事件详情',
    section: '全局能力',
    description: '审计事件实体唯一详情路由。',
    parentPath: '/admin/audit',
    entityIdParam: 'eventId',
    owner: '#751',
  },
]

export const globalRoutes: PageRoute[] = [
  {
    path: '/search',
    pageId: 'GLOBAL-SEARCH',
    title: '全局搜索',
    section: '全局能力',
    description: '跨实体搜索 API 尚待 #751 核实；当前提供页面入口检索。',
    owner: '#751',
  },
  {
    path: '/notifications',
    pageId: 'GLOBAL-NOTIFICATIONS',
    title: '通知中心',
    section: '全局能力',
    description: '通知列表与已读 API 尚待 #751 核实；不伪造通知数据。',
    owner: '#751',
  },
  {
    path: '/admin/audit',
    pageId: 'GLOBAL-AUDIT',
    title: '审计中心',
    section: '全局能力',
    description: '审计事件查询入口。',
    owner: '#751',
  },
  {
    path: '/admin/operations',
    pageId: 'GLOBAL-OPS',
    title: '系统运维',
    section: '全局能力',
    description: '系统诊断与运维事件入口。',
    owner: '#751',
  },
  {
    path: '/admin/scheduler/jobs',
    pageId: 'GLOBAL-SCHEDULER-JOBS',
    title: '调度任务',
    section: '全局能力',
    description: '调度任务 — 巡检、逾期扫描、死信等定时任务。',
    owner: '#751',
  },
  {
    path: '/admin/scheduler/subscriptions',
    pageId: 'GLOBAL-SCHEDULER-SUBS',
    title: '事件订阅',
    section: '全局能力',
    description: '事件订阅 — 事件通知通道。',
    owner: '#751',
  },
]

export interface SubPageNav {
  label: string
  path: string
  authOnly?: boolean
  capability?: string
  permission?: string
}

export interface NavigationSection {
  id:
    | 'home'
    | 'office'
    | 'tasks'
    | 'collaboration'
    | 'workflow'
    | 'cost'
    | 'administration'
  label: string
  icon: string
  path: string
  subpages: SubPageNav[]
}

/** 一级导航按演示首页合并为 7 个入口，原路由作为子页面挂载 */
export const navigationSections: NavigationSection[] = [
  {
    id: 'home',
    label: '工作台',
    icon: '⌂',
    path: '/workspace/overview',
    subpages: [
      { label: '概览', path: '/workspace/overview' },
      { label: '快速使用', path: '/workspace/quickstart' },
      { label: '运行看板', path: '/workspace/status' },
    ],
  },
  {
    id: 'office',
    label: '3D 办公室',
    icon: '▣',
    path: '/workspace/office',
    subpages: [],
  },
  {
    id: 'tasks',
    label: '任务中心',
    icon: '☰',
    path: '/workspace/tasks',
    subpages: [
      { label: '任务列表', path: '/workspace/tasks' },
      { label: '批量执行', path: '/operations/batches' },
      { label: '运行回放', path: '/operations/replay' },
      { label: '审批中心', path: '/operations/approvals' },
      { label: '会话与消息', path: '/operations/conversations' },
      { label: '文件与产物', path: '/operations/files' },
    ],
  },
  {
    id: 'collaboration',
    label: '智能体协作',
    icon: '⚇',
    path: '/workspace/collaboration',
    subpages: [
      { label: '协作中心', path: '/workspace/collaboration' },
      { label: '智能体运行态', path: '/operations/agents' },
      { label: '知识库', path: '/operations/knowledge-bases' },
      { label: '知识图谱', path: '/operations/knowledge-graph' },
      { label: 'RAG 实验台', path: '/operations/rag-playground' },
      { label: '记忆治理', path: '/operations/memories' },
    ],
  },
  {
    id: 'workflow',
    label: '工作流',
    icon: '⌘',
    path: '/admin/workflows',
    subpages: [
      { label: '工作流设计', path: '/admin/workflows' },
      {
        label: '提示词模板',
        path: '/admin/prompts',
        capability: 'prompt_management',
      },
    ],
  },
  {
    id: 'cost',
    label: '成本分摊',
    icon: '¥',
    path: '/workspace/quality-cost',
    subpages: [
      { label: '成本与质量', path: '/workspace/quality-cost' },
      { label: '评测数据集', path: '/operations/evaluation/datasets' },
      { label: '评测运行', path: '/operations/evaluation/runs' },
      { label: '评测洞察', path: '/operations/evaluation/insights' },
    ],
  },
  {
    id: 'administration',
    label: '系统管理',
    icon: '⚙',
    path: '/admin/agents',
    subpages: [
      { label: '智能体定义', path: '/admin/agents' },
      { label: '模型供应商', path: '/admin/models/providers' },
      { label: '模型模板', path: '/admin/models/templates' },
      { label: '模型路由', path: '/admin/models/routing' },
      { label: '工具与 MCP', path: '/admin/tools' },
      { label: '技能包', path: '/admin/skills' },
      {
        label: '组织与成员',
        path: '/admin/organization',
        authOnly: true,
        permission: 'platform.organization.read',
      },
      {
        label: '角色与权限',
        path: '/admin/access-control',
        authOnly: true,
        permission: 'platform.rbac.read',
      },
      { label: '系统设置', path: '/admin/settings' },
      { label: '告警中心', path: '/operations/alerts' },
      { label: '扩展发布者', path: '/admin/extensions' },
      { label: '审计中心', path: '/admin/audit' },
      { label: '数据连接器', path: '/admin/connectors' },
      { label: 'Webhook 订阅', path: '/admin/webhooks' },
      { label: '调度任务', path: '/admin/scheduler/jobs' },
      { label: '事件订阅', path: '/admin/scheduler/subscriptions' },
      { label: '系统运维', path: '/admin/operations' },
    ],
  },
]

export function getNavigationSections(
  authEnabled: boolean,
  capabilities: string[] = [],
  permissions: readonly string[] = [],
): NavigationSection[] {
  return navigationSections.map((section) => ({
    ...section,
    subpages: section.subpages.filter(
      (page) =>
        (!page.authOnly || authEnabled) &&
        (!page.capability || capabilities.includes(page.capability)) &&
        (!page.permission || permissions.includes(page.permission)),
    ),
  }))
}

function matchesPath(base: string, pathname: string): boolean {
  return pathname === base || pathname.startsWith(`${base}/`)
}

export function findNavigationSection(
  pathname: string,
): NavigationSection | undefined {
  return navigationSections.find(
    (section) =>
      matchesPath(section.path, pathname) ||
      section.subpages.some((page) => matchesPath(page.path, pathname)),
  )
}

export function findSubPage(
  section: NavigationSection | undefined,
  pathname: string,
): SubPageNav | undefined {
  if (!section) return undefined
  return (
    section.subpages.find((page) => matchesPath(page.path, pathname)) ??
    section.subpages.find((page) => page.path === section.path)
  )
}

export const authRoutes: PageRoute[] = [
  {
    path: '/auth/login',
    pageId: 'AUTH-LOGIN',
    title: '登录',
    section: '身份',
    description: '仅在服务端 auth.enabled=true 时启用登录入口。',
    owner: '#743',
    authOnly: true,
  },
  {
    path: '/auth/initialize',
    pageId: 'AUTH-INITIALIZE',
    title: '管理员初始化',
    section: '身份',
    description: '仅权限模式且服务端报告需要初始化时启用。',
    owner: '#743',
    authOnly: true,
  },
]

export function getVisibleNavigation(
  authEnabled: boolean,
  capabilities: string[] = [],
): NavigationGroup[] {
  const groups: NavigationGroup[] = [
    {
      id: 'workspace',
      title: '工作空间',
      items: pageRoutes.filter(
        (item) => item.section === '工作空间' && !item.path.includes('/:'),
      ),
    },
    {
      id: 'operations',
      title: '智能体运营',
      items: pageRoutes.filter(
        (item) => item.section === '智能体运营' && !item.path.includes('/:'),
      ),
    },
    {
      id: 'administration',
      title: '平台管理',
      items: pageRoutes.filter(
        (item) =>
          item.section === '平台管理' &&
          !item.path.includes('/:') &&
          (!item.authOnly || authEnabled) &&
          (!item.capability || capabilities.includes(item.capability)),
      ),
    },
    {
      id: 'global',
      title: '全局能力',
      items: globalRoutes.filter(
        (item) =>
          !item.path.includes('/:') &&
          !['/search', '/notifications'].includes(item.path),
      ),
    },
  ]
  return groups
}

export function getAllRoutes(authEnabled: boolean): PageRoute[] {
  return [
    ...pageRoutes,
    ...detailRoutes,
    ...globalRoutes,
    ...(authEnabled ? authRoutes : []),
  ]
}

export function findPageRoute(path: string): PageRoute | undefined {
  return [...pageRoutes, ...globalRoutes, ...authRoutes].find(
    (route) => route.path === path,
  )
}

export function isCanonicalRouteSetValid(
  routes: PageRoute[] = [...pageRoutes, ...globalRoutes, ...authRoutes],
): boolean {
  const paths = routes.map((route) => route.path)
  const ids = routes.map((route) => route.pageId)
  return (
    paths.length === new Set(paths).size && ids.length === new Set(ids).size
  )
}
