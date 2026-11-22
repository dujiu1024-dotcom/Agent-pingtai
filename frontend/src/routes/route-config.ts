export interface PageRoute {
  path: string
  pageId: string
  title: string
  section: string
  description: string
  parentPath?: string
  authOnly?: boolean
  capability?: string
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
    title: '指令中心',
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
    title: 'Agent 协作中心',
    section: '工作空间',
    description: 'Agent、Workflow 运行与协作态势入口。',
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
    title: 'Agent 运行态',
    section: '智能体运营',
    description: 'Agent Registry 运行态列表，展示发布状态、版本与可用的运行信息。',
    owner: '#808',
  },
  {
    path: '/operations/agents/:agentId',
    pageId: 'OPS-AGENT-DETAIL',
    title: 'Agent 运行详情',
    section: '智能体运营',
    description: 'Agent Registry active version 详情与定义信息。',
    parentPath: '/operations/agents',
    entityIdParam: 'agentId',
    owner: '#808',
  },
  {
    path: '/operations/tasks/:taskId',
    pageId: 'WS-TASK-DETAIL',
    title: '任务详情',
    section: '智能体运营',
    description: 'Task 实体唯一详情路由。',
    parentPath: '/workspace/tasks',
    entityIdParam: 'taskId',
    owner: '#745',
  },
  {
    path: '/operations/runs/:runId',
    pageId: 'WS-RUN-DETAIL',
    title: 'Run Trace 详情',
    section: '智能体运营',
    description: 'Run 实体唯一详情路由。',
    parentPath: '/workspace/tasks',
    entityIdParam: 'runId',
    owner: '#745',
  },
  {
    path: '/operations/workflow-runs/:runId',
    pageId: 'WS-WORKFLOW-RUN',
    title: 'Workflow 运行详情',
    section: '智能体运营',
    description: 'Workflow 运行实例详情。',
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
    description: 'Approval 实体唯一详情路由。',
    parentPath: '/operations/approvals',
    entityIdParam: 'approvalId',
    owner: '#750',
  },
  {
    path: '/operations/knowledge-bases',
    pageId: 'OPS-KNOWLEDGE',
    title: '知识库',
    section: '智能体运营',
    description: 'Knowledge collections、documents、chunks 与检索。',
    owner: '#760',
  },
  {
    path: '/operations/documents/:documentId',
    pageId: 'OPS-DOCUMENT',
    title: '文档详情',
    section: '智能体运营',
    description: 'Document 实体唯一详情路由。',
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
    owner: '#802',
  },
  {
    path: '/admin/agents',
    pageId: 'ADM-AGENTS',
    title: 'Agent 定义',
    section: '平台管理',
    description:
      'Agent 定义 CRUD、指令版本、模型模板与工具/Skill/Workflow 关联。',
    owner: '#757',
  },
  {
    path: '/admin/workflows',
    pageId: 'ADM-WORKFLOWS',
    title: 'Workflow 设计',
    section: '平台管理',
    description: 'Workflow 定义列表与可视化设计器入口。',
    owner: '#758',
  },
  {
    path: '/admin/models/providers',
    pageId: 'ADM-MODEL-PROVIDERS',
    title: '模型供应商',
    section: '平台管理',
    description: 'Model Provider Registry 列表、版本与配置详情。',
    owner: '#804',
  },
  {
    path: '/admin/models/templates',
    pageId: 'ADM-MODEL-TEMPLATES',
    title: '模型模板与目录',
    section: '平台管理',
    description: 'Model Template Registry 列表、版本与配置详情。',
    owner: '#804',
  },
  {
    path: '/admin/models/routing',
    pageId: 'ADM-MODEL-ROUTING',
    title: '模型路由策略',
    section: '平台管理',
    description: '模型路由策略入口；当前后端尚未提供路由策略 Registry API。',
    owner: '#804',
  },
  {
    path: '/admin/tools',
    pageId: 'ADM-TOOLS',
    title: '工具与 MCP',
    section: '平台管理',
    description: '查看已注册工具、MCP Server 元数据与 JSON Schema。',
    owner: '#805',
  },
  {
    path: '/admin/skills',
    pageId: 'ADM-SKILLS',
    title: 'Skills',
    section: '平台管理',
    description: 'Skill 管理入口。',
    owner: '#806',
  },
  {
    path: '/admin/organization',
    pageId: 'ADM-ORG',
    title: '组织与成员',
    section: '平台管理',
    description: '组织、部门、项目与成员管理；仅权限模式可用。',
    owner: '#749',
    authOnly: true,
  },
  {
    path: '/admin/access-control',
    pageId: 'ADM-RBAC',
    title: '角色与权限',
    section: '平台管理',
    description: '角色与权限管理；仅权限模式可用。',
    owner: '#749',
    authOnly: true,
  },
  {
    path: '/admin/settings',
    pageId: 'SYS-SETTINGS',
    title: '系统设置',
    section: '平台管理',
    description: '查看服务端启动配置、租户配额与平台资源统计；只读且仅权限模式开放。',
    owner: '#812',
    authOnly: true,
  },
  {
    path: '/operations/blackboard/:entryId',
    pageId: 'WS-BLACKBOARD',
    title: 'Blackboard 条目详情',
    section: '智能体运营',
    description: 'Blackboard 条目唯一详情路由。',
    parentPath: '/workspace/collaboration',
    entityIdParam: 'entryId',
    owner: '#746',
  },
  {
    path: '/admin/prompts',
    pageId: 'ADM-PROMPTS',
    title: 'Prompt 与模板管理',
    section: '平台管理',
    description: 'Prompt Registry 条件能力入口。',
    owner: '#807',
    capability: 'prompt_management',
  },
  {
    path: '/operations/evaluation/datasets',
    pageId: 'OPS-EVAL-DATASETS',
    title: '评测数据集',
    section: '智能体运营',
    description: 'Evaluation Dataset 列表入口。',
    owner: '#803',
  },
  {
    path: '/operations/evaluation/runs',
    pageId: 'OPS-EVAL-RUNS',
    title: '评测运行与样本',
    section: '智能体运营',
    description: 'Evaluation Run 列表入口。',
    owner: '#803',
  },
]

export const detailRoutes: PageRoute[] = [
  {
    path: '/admin/prompts/:promptId',
    pageId: 'ADM-PROMPT-DETAIL',
    title: 'Prompt 详情',
    section: '平台管理',
    description: 'Prompt 版本、模板内容与评测 Gate 状态。',
    parentPath: '/admin/prompts',
    entityIdParam: 'promptId',
    owner: '#807',
    capability: 'prompt_management',
  },
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
    description: 'Dataset 唯一详情路由。',
    parentPath: '/operations/evaluation/datasets',
    entityIdParam: 'datasetId',
    owner: '#803',
  },
  {
    path: '/operations/evaluation/runs/:evaluationRunId',
    pageId: 'OPS-EVAL-RUNS',
    title: '评测运行详情',
    section: '智能体运营',
    description: 'Evaluation Run 唯一详情路由。',
    parentPath: '/operations/evaluation/runs',
    entityIdParam: 'evaluationRunId',
    owner: '#803',
  },
  {
    path: '/admin/models/providers/:providerId',
    pageId: 'ADM-MODEL-PROVIDERS',
    title: '模型供应商详情',
    section: '平台管理',
    description: 'Model Provider active 配置与版本历史。',
    parentPath: '/admin/models/providers',
    entityIdParam: 'providerId',
    owner: '#804',
  },
  {
    path: '/admin/models/templates/:templateId',
    pageId: 'ADM-MODEL-TEMPLATES',
    title: '模型模板详情',
    section: '平台管理',
    description: 'Model Template active 配置与版本历史。',
    parentPath: '/admin/models/templates',
    entityIdParam: 'templateId',
    owner: '#804',
  },
  {
    path: '/admin/models/routing/:policyId',
    pageId: 'ADM-MODEL-ROUTING',
    title: '模型路由策略详情',
    section: '平台管理',
    description: '模型路由策略详情；等待后端 API 契约。',
    parentPath: '/admin/models/routing',
    entityIdParam: 'policyId',
    owner: '#804',
  },
  {
    path: '/admin/tools/:toolId',
    pageId: 'ADM-TOOLS',
    title: '工具详情',
    section: '平台管理',
    description: 'Tool/MCP Server 唯一详情路由。',
    parentPath: '/admin/tools',
    entityIdParam: 'toolId',
    owner: '#805',
  },
  {
    path: '/admin/skills/:skillId',
    pageId: 'ADM-SKILLS',
    title: 'Skill 详情',
    section: '平台管理',
    description: 'Skill 唯一详情路由。',
    parentPath: '/admin/skills',
    entityIdParam: 'skillId',
    owner: '#806',
  },
  {
    path: '/admin/agents/:agentId',
    pageId: 'ADM-AGENT-DETAIL',
    title: 'Agent 定义详情',
    section: '平台管理',
    description: 'Agent 定义详情、配置与版本历史。',
    parentPath: '/admin/agents',
    entityIdParam: 'agentId',
    owner: '#757',
  },
  {
    path: '/admin/workflows/:workflowId/edit',
    pageId: 'ADM-WORKFLOW-EDITOR',
    title: 'Workflow 设计器',
    section: '平台管理',
    description: 'Workflow Definition 可视化编辑路由。',
    parentPath: '/admin/workflows',
    entityIdParam: 'workflowId',
    owner: '#758',
  },
  {
    path: '/admin/workflows/:workflowId',
    pageId: 'ADM-WORKFLOW-DETAIL',
    title: 'Workflow 定义详情',
    section: '平台管理',
    description: 'Workflow Definition 详情与版本入口。',
    parentPath: '/admin/workflows',
    entityIdParam: 'workflowId',
    owner: '#758',
  },
  {
    path: '/operations/memories/:memoryId',
    pageId: 'OPS-MEMORY',
    title: '记忆详情',
    section: '智能体运营',
    description: 'Memory 唯一详情路由。',
    parentPath: '/operations/memories',
    entityIdParam: 'memoryId',
    owner: '#750',
  },
  {
    path: '/operations/conversations/:conversationId',
    pageId: 'OPS-CONVERSATIONS',
    title: '会话详情',
    section: '智能体运营',
    description: 'Conversation 唯一详情路由。',
    parentPath: '/operations/conversations',
    entityIdParam: 'conversationId',
    owner: '#750',
  },
  {
    path: '/operations/files/:fileId',
    pageId: 'OPS-FILES',
    title: '文件与产物详情',
    section: '智能体运营',
    description: 'File/Artifact 唯一详情路由。',
    parentPath: '/operations/files',
    entityIdParam: 'fileId',
    owner: '#802',
  },
  {
    path: '/admin/audit/:eventId',
    pageId: 'GLOBAL-AUDIT',
    title: '审计事件详情',
    section: '全局能力',
    description: 'Audit Event 唯一详情路由。',
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
    path: '/admin/ops',
    pageId: 'GLOBAL-OPS',
    title: '系统运维',
    section: '全局能力',
    description: 'Metrics、组件 Health 与 Scheduler 运行态 Dashboard。',
    owner: '#813',
  },
  {
    path: '/admin/scheduler/jobs',
    pageId: 'GLOBAL-SCHEDULER-JOBS',
    title: '调度任务',
    section: '全局能力',
    description: 'Scheduler Jobs — 巡检、逾期扫描、死信等定时任务。',
    owner: '#751',
  },
  {
    path: '/admin/scheduler/subscriptions',
    pageId: 'GLOBAL-SCHEDULER-SUBS',
    title: '事件订阅',
    section: '全局能力',
    description: 'Scheduler Subscriptions — 事件订阅与通知通道。',
    owner: '#751',
  },
]

export interface SubPageNav {
  label: string
  path: string
  authOnly?: boolean
  capability?: string
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
      { label: '指令中心', path: '/workspace/quickstart' },
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
      { label: '审批中心', path: '/operations/approvals' },
      { label: '会话与消息', path: '/operations/conversations' },
      { label: '文件与产物', path: '/operations/files' },
    ],
  },
  {
    id: 'collaboration',
    label: 'Agent 协作',
    icon: '⚇',
    path: '/workspace/collaboration',
    subpages: [
      { label: '协作中心', path: '/workspace/collaboration' },
      { label: 'Agent 运行态', path: '/operations/agents' },
      { label: '知识库', path: '/operations/knowledge-bases' },
      { label: '记忆治理', path: '/operations/memories' },
    ],
  },
  {
    id: 'workflow',
    label: 'Workflow',
    icon: '⌘',
    path: '/admin/workflows',
    subpages: [
      { label: 'Workflow 设计', path: '/admin/workflows' },
      {
        label: 'Prompt 模板',
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
    ],
  },
  {
    id: 'administration',
    label: '系统管理',
    icon: '⚙',
    path: '/admin/agents',
    subpages: [
      { label: 'Agent 定义', path: '/admin/agents' },
      { label: '模型供应商', path: '/admin/models/providers' },
      { label: '模型模板', path: '/admin/models/templates' },
      { label: '模型路由', path: '/admin/models/routing' },
      { label: '工具与 MCP', path: '/admin/tools' },
      { label: 'Skills', path: '/admin/skills' },
      {
        label: '组织与成员',
        path: '/admin/organization',
        authOnly: true,
      },
      {
        label: '角色与权限',
        path: '/admin/access-control',
        authOnly: true,
      },
      { label: '系统设置', path: '/admin/settings' },
      { label: '审计中心', path: '/admin/audit' },
      { label: '调度任务', path: '/admin/scheduler/jobs' },
      { label: '事件订阅', path: '/admin/scheduler/subscriptions' },
      { label: '系统运维', path: '/admin/ops' },
    ],
  },
]

export function getNavigationSections(
  authEnabled: boolean,
  capabilities: string[] = [],
): NavigationSection[] {
  return navigationSections.map((section) => ({
    ...section,
    subpages: section.subpages.filter(
      (page) =>
        (!page.authOnly || authEnabled) &&
        (!page.capability || capabilities.includes(page.capability)),
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
    owner: '#809',
    authOnly: true,
  },
  {
    path: '/auth/initialize',
    pageId: 'AUTH-INITIALIZE',
    title: '管理员初始化',
    section: '身份',
    description: '仅权限模式且服务端报告需要初始化时启用。',
    owner: '#809',
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
