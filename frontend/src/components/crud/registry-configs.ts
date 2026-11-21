import type { RegistryKindConfig } from './RegistryCatalogPage'

export const registryConfigs: Record<string, RegistryKindConfig> = {
  '/admin/models/providers': {
    kind: 'model_provider',
    title: '模型供应商',
    description:
      '配置模型供应商连接信息。密钥请填写 secret 引用字段，禁止写入明文。',
    itemLabel: '供应商',
    detailPath: (itemId) =>
      `/admin/models/providers/${encodeURIComponent(itemId)}`,
    template: {
      name: '',
      provider_type: 'openai-compatible',
      base_url: '',
      api_key_ref: '',
    },
  },
  '/admin/models/templates': {
    kind: 'model_template',
    title: '模型模板与目录',
    description: '配置可被 Agent 引用的模型模板。',
    itemLabel: '模型模板',
    detailPath: (itemId) =>
      `/admin/models/templates/${encodeURIComponent(itemId)}`,
    template: {
      name: '',
      provider_ref: '',
      model: '',
      temperature: 0.7,
      max_tokens: 2048,
    },
  },
  '/admin/models/routing': {
    kind: 'routing_policy',
    title: '模型路由策略',
    description: '配置模型路由规则，按条件选择模型模板。',
    itemLabel: '路由策略',
    detailPath: (itemId) =>
      `/admin/models/routing/${encodeURIComponent(itemId)}`,
    template: {
      name: '',
      rules: [],
      fallback_template_ref: '',
    },
  },
  '/admin/tools': {
    kind: 'tool',
    title: '工具与 MCP',
    description:
      '注册工具元数据与 MCP Server 配置。运行时不会从定义中加载可执行代码。',
    itemLabel: '工具',
    detailPath: (itemId) => `/admin/tools/${encodeURIComponent(itemId)}`,
    template: {
      name: '',
      description: '',
      risk_level: 'read_only',
      input_schema: {},
    },
    extraColumns: [
      {
        key: 'description',
        label: '描述',
        render: (d: Record<string, unknown>) =>
          String(d.description ?? '—').slice(0, 60),
      },
      {
        key: 'risk_level',
        label: '风险',
        render: (d: Record<string, unknown>) =>
          String(d.risk_level ?? '—'),
      },
      {
        key: 'used_by',
        label: '被谁用',
        render: (_d: Record<string, unknown>, extra?: Record<string, unknown>) => {
          // 优先用 RegistryCatalogPage 注入的 agent 中文名
          const names = (extra?.used_by_names as string[]) ?? []
          if (names.length > 0) return names.join('、')
          const raw = (extra?.used_by as string[]) ?? []
          if (raw.length === 0) return '—'
          return raw.join(', ')
        },
      },
    ],
  },
  '/admin/skills': {
    kind: 'skill',
    title: '技能包',
    description: '注册技能包清单与依赖。',
    itemLabel: '技能包',
    detailPath: (itemId) => `/admin/skills/${encodeURIComponent(itemId)}`,
    template: {
      name: '',
      manifest: {
        name: '',
        dependencies: {},
      },
    },
    extraColumns: [
      {
        key: 'description',
        label: '描述',
        render: (d: Record<string, unknown>) =>
          String(d.description ?? '—').slice(0, 60),
      },
      {
        key: 'mode',
        label: '模式',
        render: (d: Record<string, unknown>) =>
          String(d.mode ?? '—'),
      },
      {
        key: 'tool_ids',
        label: '打包的工具',
        render: (_d: Record<string, unknown>, extra?: Record<string, unknown>) => {
          // 优先用 RegistryCatalogPage 注入的 tool 中文名
          const names = (extra?.tool_names as string[]) ?? []
          if (names.length > 0) return names.join('、')
          return '—'
        },
      },
      {
        key: 'used_by',
        label: '被谁绑',
        render: (_d: Record<string, unknown>, extra?: Record<string, unknown>) => {
          const raw = (extra?.used_by as string[]) ?? []
          if (raw.length === 0) return '—'
          return raw.join(', ')
        },
      },
    ],
  },
}
