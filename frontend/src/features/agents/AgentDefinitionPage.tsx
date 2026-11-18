import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { EmptyState } from '@/components/States'
import './agent-definition.css'

type DefinitionStatus = 'draft' | 'published' | 'disabled'
type AgentDefinition = {
  agent_id: string
  name: string
  description: string
  instructions: string
  model_template_ref: string
  tool_refs: string[]
  skill_refs: string[]
  workflow_refs: string[]
  input_schema: string
  output_schema: string
  status: DefinitionStatus
  revision: number
  updated_at: string
}
type DefinitionVersion = {
  version: number
  created_at: string
  definition: AgentDefinition
}
type DraftForm = Omit<AgentDefinition, 'revision' | 'updated_at'>

const emptyDraft = (): DraftForm => ({
  agent_id: '',
  name: '',
  description: '',
  instructions: '',
  model_template_ref: '',
  tool_refs: [],
  skill_refs: [],
  workflow_refs: [],
  input_schema: '{\n  "type": "object",\n  "properties": {}\n}',
  output_schema: '{\n  "type": "object",\n  "properties": {}\n}',
  status: 'draft',
})
const splitRefs = (value: string) =>
  [...new Set(value.split(',').map((item) => item.trim()).filter(Boolean))]
const refsText = (items: string[]) => items.join(', ')
const prettyTime = (value: string) =>
  value ? new Date(value).toLocaleString() : '—'

function parseSchema(value: string, label: string): string | null {
  try {
    const parsed: unknown = JSON.parse(value)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
      return label + ' 必须是 JSON object。'
    return null
  } catch {
    return label + ' 不是有效 JSON。'
  }
}

function VersionDiff({
  before,
  after,
}: {
  before: AgentDefinition
  after: AgentDefinition
}) {
  const fields: (keyof AgentDefinition)[] = [
    'name',
    'description',
    'instructions',
    'model_template_ref',
    'tool_refs',
    'skill_refs',
    'workflow_refs',
    'input_schema',
    'output_schema',
    'status',
  ]
  const changes = fields.filter(
    (field) =>
      JSON.stringify(before[field]) !== JSON.stringify(after[field]),
  )
  return changes.length ? (
    <div className="agent-diff">
      {changes.map((field) => (
        <article key={field}>
          <strong>{field}</strong>
          <div className="agent-diff-columns">
            <pre>{JSON.stringify(before[field], null, 2)}</pre>
            <pre>{JSON.stringify(after[field], null, 2)}</pre>
          </div>
        </article>
      ))}
    </div>
  ) : (
    <p className="agent-muted">两个版本没有字段差异。</p>
  )
}

export function AgentDefinitionPage() {
  const { agentId: routeAgentId } = useParams()
  const [definitions, setDefinitions] = useState<AgentDefinition[]>([])
  const [versions, setVersions] = useState<Record<string, DefinitionVersion[]>>(
    {},
  )
  const [selectedId, setSelectedId] = useState('')
  const [form, setForm] = useState<DraftForm>(emptyDraft)
  const [toolText, setToolText] = useState('')
  const [skillText, setSkillText] = useState('')
  const [workflowText, setWorkflowText] = useState('')
  const [editing, setEditing] = useState(false)
  const [showEditor, setShowEditor] = useState(false)
  const [activeTab, setActiveTab] = useState<'definition' | 'versions'>(
    'definition',
  )
  const [compareFrom, setCompareFrom] = useState('')
  const [compareTo, setCompareTo] = useState('')
  const [validationError, setValidationError] = useState('')

  const selected = definitions.find((item) => item.agent_id === selectedId)
  const history = versions[selectedId] ?? []
  const versionBefore = history.find(
    (item) => String(item.version) === compareFrom,
  )
  const versionAfter = history.find(
    (item) => String(item.version) === compareTo,
  )
  const versionOptions = useMemo(
    () => history.map((item) => item.version),
    [history],
  )

  useEffect(() => {
    if (routeAgentId) {
      setSelectedId(routeAgentId)
      setShowEditor(false)
    }
  }, [routeAgentId])

  const beginCreate = () => {
    setSelectedId('')
    setForm(emptyDraft())
    setToolText('')
    setSkillText('')
    setWorkflowText('')
    setValidationError('')
    setEditing(false)
    setShowEditor(true)
    setActiveTab('definition')
  }

  const beginEdit = (item: AgentDefinition) => {
    setSelectedId(item.agent_id)
    setForm({
      agent_id: item.agent_id,
      name: item.name,
      description: item.description,
      instructions: item.instructions,
      model_template_ref: item.model_template_ref,
      tool_refs: item.tool_refs,
      skill_refs: item.skill_refs,
      workflow_refs: item.workflow_refs,
      input_schema: item.input_schema,
      output_schema: item.output_schema,
      status: 'draft',
    })
    setToolText(refsText(item.tool_refs))
    setSkillText(refsText(item.skill_refs))
    setWorkflowText(refsText(item.workflow_refs))
    setValidationError('')
    setEditing(true)
    setShowEditor(true)
    setActiveTab('definition')
  }

  const saveLocalDraft = () => {
    const id = form.agent_id.trim()
    if (!id || !form.name.trim()) {
      setValidationError('Agent ID 和名称为必填项。')
      return
    }
    if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(id)) {
      setValidationError('Agent ID 格式无效，仅支持字母、数字及 . _ : -。')
      return
    }
    const schemaError =
      parseSchema(form.input_schema, '输入 Schema') ??
      parseSchema(form.output_schema, '输出 Schema')
    if (schemaError) {
      setValidationError(schemaError)
      return
    }
    const now = new Date().toISOString()
    const next: AgentDefinition = {
      ...form,
      agent_id: id,
      name: form.name.trim(),
      tool_refs: splitRefs(toolText),
      skill_refs: splitRefs(skillText),
      workflow_refs: splitRefs(workflowText),
      revision: (selected?.revision ?? 0) + 1,
      updated_at: now,
      status: 'draft',
    }
    setDefinitions((current) => {
      const exists = current.some((item) => item.agent_id === id)
      return exists
        ? current.map((item) => (item.agent_id === id ? next : item))
        : [...current, next]
    })
    setVersions((current) => {
      const previous = current[id] ?? []
      return {
        ...current,
        [id]: [
          ...previous,
          { version: next.revision, created_at: now, definition: next },
        ],
      }
    })
    setSelectedId(id)
    setShowEditor(false)
    setValidationError('')
  }

  const startNewVersion = () => {
    if (!selected) return
    beginEdit(selected)
  }

  const canUseDetail = !routeAgentId || Boolean(selected)

  return (
    <section className="page-content agent-definition-page">
      <div className="page-heading agent-definition-heading">
        <div>
          <p className="eyebrow">ADMINISTRATION / AGENT DEFINITION</p>
          <h1>Agent 定义管理</h1>
          <p>管理 Agent 指令、模型模板引用、工具与 Skill 白名单及 Workflow 关联。</p>
        </div>
        <button className="button-primary" type="button" onClick={beginCreate}>
          新建 Agent
        </button>
      </div>

      <div className="agent-definition-notice" role="note">
        <strong>前端壳 / 本地草稿模式</strong>
        <span>
          当前列表从空数组开始。保存仅更新当前页面内存状态，不会写入服务端；Registry API、
          ETag/revision 并发控制、权限审计与双模式 API 装配待后端补齐。
        </span>
      </div>

      <div className="agent-definition-layout">
        <aside className="agent-definition-list">
          <div className="agent-section-heading">
            <h2>Agent 定义</h2>
            <span>{definitions.length}</span>
          </div>
          {definitions.length ? (
            definitions.map((item) => (
              <button
                className={
                  selectedId === item.agent_id
                    ? 'agent-definition-list-item active'
                    : 'agent-definition-list-item'
                }
                key={item.agent_id}
                type="button"
                onClick={() => {
                  setSelectedId(item.agent_id)
                  setShowEditor(false)
                  setActiveTab('definition')
                }}
              >
                <strong>{item.name}</strong>
                <small>{item.agent_id}</small>
                <span>{item.status === 'draft' ? '草稿' : item.status}</span>
              </button>
            ))
          ) : (
            <EmptyState
              title="暂无 Agent 定义"
              description="创建本地草稿以预览配置结构；服务端列表 API 接入后将在此显示租户内定义。"
            />
          )}
        </aside>

        <div className="agent-definition-detail">
          {showEditor ? (
            <section className="agent-definition-panel">
              <div className="agent-section-heading">
                <div>
                  <h2>{editing ? '编辑定义草稿' : '新建 Agent 定义'}</h2>
                  <p>字段结构以 Registry definition schema 为准。</p>
                </div>
                <button
                  className="button-secondary"
                  type="button"
                  onClick={() => setShowEditor(false)}
                >
                  取消
                </button>
              </div>
              <div className="agent-form-grid">
                <label>
                  Agent ID *
                  <input
                    value={form.agent_id}
                    disabled={editing}
                    onChange={(event) =>
                      setForm({ ...form, agent_id: event.target.value })
                    }
                    placeholder="例如 support-agent"
                  />
                </label>
                <label>
                  名称 *
                  <input
                    value={form.name}
                    onChange={(event) =>
                      setForm({ ...form, name: event.target.value })
                    }
                    placeholder="Agent 显示名称"
                  />
                </label>
                <label className="wide">
                  描述
                  <input
                    value={form.description}
                    onChange={(event) =>
                      setForm({ ...form, description: event.target.value })
                    }
                    placeholder="用途与职责说明"
                  />
                </label>
                <label className="wide">
                  系统指令 / Instructions
                  <textarea
                    rows={8}
                    value={form.instructions}
                    onChange={(event) =>
                      setForm({ ...form, instructions: event.target.value })
                    }
                    placeholder="定义 Agent 的目标、边界、输出要求与行为规范"
                  />
                </label>
                <label className="wide">
                  Model Template 引用
                  <input
                    value={form.model_template_ref}
                    onChange={(event) =>
                      setForm({ ...form, model_template_ref: event.target.value })
                    }
                    placeholder="model_template_ref（不填写密钥）"
                  />
                  <small>仅保存模板引用；密钥由服务端 secret reference 管理。</small>
                </label>
                <label className="wide">
                  Tool 白名单引用
                  <input
                    value={toolText}
                    onChange={(event) => setToolText(event.target.value)}
                    placeholder="tool-a, tool-b"
                  />
                </label>
                <label className="wide">
                  Skill 白名单引用
                  <input
                    value={skillText}
                    onChange={(event) => setSkillText(event.target.value)}
                    placeholder="skill-a, skill-b"
                  />
                </label>
                <label className="wide">
                  关联 Workflow
                  <input
                    value={workflowText}
                    onChange={(event) => setWorkflowText(event.target.value)}
                    placeholder="workflow-id, workflow-id-2"
                  />
                </label>
                <label>
                  输入 Schema (JSON)
                  <textarea
                    rows={9}
                    spellCheck={false}
                    value={form.input_schema}
                    onChange={(event) =>
                      setForm({ ...form, input_schema: event.target.value })
                    }
                  />
                </label>
                <label>
                  输出 Schema (JSON)
                  <textarea
                    rows={9}
                    spellCheck={false}
                    value={form.output_schema}
                    onChange={(event) =>
                      setForm({ ...form, output_schema: event.target.value })
                    }
                  />
                </label>
              </div>
              {validationError && (
                <p className="agent-form-error" role="alert">
                  {validationError}
                </p>
              )}
              <div className="agent-form-actions">
                <button
                  className="button-primary"
                  type="button"
                  onClick={saveLocalDraft}
                >
                  保存本地草稿
                </button>
                <span>本地校验仅检查必填字段与 JSON 格式，不替代服务端引用校验。</span>
              </div>
            </section>
          ) : selected && canUseDetail ? (
            <>
              <section className="agent-definition-panel">
                <div className="agent-detail-title">
                  <div>
                    <p className="eyebrow">{selected.agent_id}</p>
                    <h2>{selected.name}</h2>
                    <p>{selected.description || '暂无描述'}</p>
                  </div>
                  <span className="agent-status">{selected.status}</span>
                </div>
                <div className="agent-detail-actions">
                  <button
                    className="button-secondary"
                    type="button"
                    onClick={() => beginEdit(selected)}
                  >
                    编辑草稿
                  </button>
                  <button
                    className="button-secondary"
                    type="button"
                    disabled
                    title="等待 Registry 发布 API"
                  >
                    发布（待 API）
                  </button>
                  <button
                    className="button-secondary"
                    type="button"
                    disabled
                    title="等待 Registry 停用 API"
                  >
                    停用（待 API）
                  </button>
                  <button
                    className="button-secondary"
                    type="button"
                    disabled
                    title="等待 Registry 回滚 API"
                  >
                    回滚（待 API）
                  </button>
                </div>
                <div className="agent-detail-meta">
                  <span>Revision {selected.revision}</span>
                  <span>更新于 {prettyTime(selected.updated_at)}</span>
                  <span>版本快照 {history.length}</span>
                </div>
                <div className="agent-detail-tabs">
                  <button
                    className={activeTab === 'definition' ? 'active' : ''}
                    type="button"
                    onClick={() => setActiveTab('definition')}
                  >
                    定义配置
                  </button>
                  <button
                    className={activeTab === 'versions' ? 'active' : ''}
                    type="button"
                    onClick={() => setActiveTab('versions')}
                  >
                    指令与版本
                  </button>
                </div>
                {activeTab === 'definition' ? (
                  <div className="agent-detail-grid">
                    <article><small>Model Template</small><strong>{selected.model_template_ref || '未绑定'}</strong></article>
                    <article><small>Tools</small><strong>{selected.tool_refs.join(', ') || '未绑定'}</strong></article>
                    <article><small>Skills</small><strong>{selected.skill_refs.join(', ') || '未绑定'}</strong></article>
                    <article><small>Workflows</small><strong>{selected.workflow_refs.join(', ') || '未关联'}</strong></article>
                    <article className="wide"><small>Instructions</small><pre>{selected.instructions || '暂无指令'}</pre></article>
                    <article><small>Input Schema</small><pre>{selected.input_schema}</pre></article>
                    <article><small>Output Schema</small><pre>{selected.output_schema}</pre></article>
                  </div>
                ) : (
                  <div className="agent-version-workspace">
                    <div className="agent-section-heading">
                      <div><h3>版本快照</h3><p>本地草稿快照；服务端版本历史 API 待接入。</p></div>
                      <button className="button-secondary" type="button" onClick={startNewVersion}>创建新草稿版本</button>
                    </div>
                    {history.length ? (
                      <>
                        <div className="agent-version-list">
                          {history.map((item) => (
                            <article key={item.version}>
                              <strong>v{item.version}</strong>
                              <span>{prettyTime(item.created_at)}</span>
                              <small>draft · local</small>
                            </article>
                          ))}
                        </div>
                        {history.length > 1 && (
                          <div className="agent-version-compare">
                            <label>基准版本<select value={compareFrom} onChange={(event) => setCompareFrom(event.target.value)}><option value="">选择版本</option>{versionOptions.map((version) => <option key={version} value={version}>v{version}</option>)}</select></label>
                            <label>目标版本<select value={compareTo} onChange={(event) => setCompareTo(event.target.value)}><option value="">选择版本</option>{versionOptions.map((version) => <option key={version} value={version}>v{version}</option>)}</select></label>
                          </div>
                        )}
                        {versionBefore && versionAfter && <VersionDiff before={versionBefore.definition} after={versionAfter.definition} />}
                      </>
                    ) : (
                      <EmptyState title="暂无版本快照" description="保存第一个本地草稿后会生成页面内版本快照。" />
                    )}
                  </div>
                )}
              </section>
            </>
          ) : routeAgentId ? (
            <EmptyState
              title="未找到该 Agent 定义"
              description="当前本地列表为空。服务端详情 API 接入后可通过深链刷新加载定义。"
              action={<Link className="text-link" to="/admin/agents">返回 Agent 定义列表</Link>}
            />
          ) : (
            <EmptyState
              title="选择或创建 Agent 定义"
              description="Agent 定义与运行态分离。运行状态、Run Trace 和协作执行信息请前往 Agent 协作中心。"
            />
          )}
        </div>
      </div>
    </section>
  )
}
