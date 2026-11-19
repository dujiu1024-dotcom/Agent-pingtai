import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { EmptyState } from '@/components/States'
import './workflow-designer.css'

type NodeKind = 'agent' | 'tool' | 'condition' | 'parallel' | 'join' | 'approval'
type WorkflowNode = {
  id: string
  name: string
  node_type: NodeKind
  version: number
  config_schema: string
  input_schema: string
  output_schema: string
  ports: string[]
  permissions: string[]
  risk: 'low' | 'medium' | 'high'
  error_retry: { strategy: 'none' | 'retry'; max_attempts: number }
}
type WorkflowEdge = { id: string; source: string; target: string; condition: string }
type WorkflowDraft = {
  workflow_id: string
  name: string
  description: string
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
}
type Snapshot = { version: number; savedAt: string; draft: WorkflowDraft }

const palette: { type: NodeKind; title: string; detail: string }[] = [
  { type: 'agent', title: 'Agent 节点', detail: '调用已发布 Agent 定义' },
  { type: 'tool', title: 'Tool 节点', detail: '受控工具/MCP 调用' },
  { type: 'condition', title: '条件分支', detail: '按条件选择后继路径' },
  { type: 'parallel', title: '并行分支', detail: '并发 fan-out' },
  { type: 'join', title: 'Join 汇合', detail: '等待并行分支汇合' },
  { type: 'approval', title: '人工审批', detail: '中断并等待审批决议' },
]
const newNode = (type: NodeKind, index: number): WorkflowNode => ({
  id: type + '-' + index,
  name: palette.find((item) => item.type === type)?.title ?? type,
  node_type: type,
  version: 1,
  config_schema: '{\n  "type": "object",\n  "properties": {}\n}',
  input_schema: '{\n  "type": "object",\n  "properties": {}\n}',
  output_schema: '{\n  "type": "object",\n  "properties": {}\n}',
  ports: type === 'condition' ? ['true', 'false'] : ['next'],
  permissions: [],
  risk: type === 'tool' || type === 'approval' ? 'medium' : 'low',
  error_retry: { strategy: 'none', max_attempts: 0 },
})
const emptyDraft = (id: string): WorkflowDraft => ({
  workflow_id: id || 'workflow-draft',
  name: '',
  description: '',
  nodes: [],
  edges: [],
})
const clone = (value: WorkflowDraft): WorkflowDraft =>
  JSON.parse(JSON.stringify(value)) as WorkflowDraft

function validate(draft: WorkflowDraft): string[] {
  const issues: string[] = []
  if (!draft.name.trim()) issues.push('Workflow 名称不能为空。')
  if (!draft.nodes.length) issues.push('至少添加一个节点。')
  const ids = new Set<string>()
  for (const node of draft.nodes) {
    if (ids.has(node.id)) issues.push('节点 ID 重复：' + node.id)
    ids.add(node.id)
    if (!node.node_type || node.version < 1) issues.push(node.name + ' 缺少有效类型版本。')
    for (const [label, value] of [['Config', node.config_schema], ['Input', node.input_schema], ['Output', node.output_schema]]) {
      try {
        const parsed: unknown = JSON.parse(value)
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
          issues.push(node.name + ' 的 ' + label + ' Schema 必须是 JSON object。')
      } catch {
        issues.push(node.name + ' 的 ' + label + ' Schema 不是有效 JSON。')
      }
    }
    if (node.error_retry.max_attempts < 0) issues.push(node.name + ' 的重试次数不能为负数。')
  }
  for (const edge of draft.edges) {
    if (!ids.has(edge.source) || !ids.has(edge.target))
      issues.push('连线 ' + edge.id + ' 引用了不存在的节点。')
    if (edge.source === edge.target) issues.push('不允许节点连接自身：' + edge.source)
  }
  return issues
}

export function WorkflowDesignerPage() {
  const { workflowId = '' } = useParams()
  const [draft, setDraft] = useState<WorkflowDraft>(() => emptyDraft(workflowId))
  const [selectedId, setSelectedId] = useState('')
  const [sourceId, setSourceId] = useState('')
  const [targetId, setTargetId] = useState('')
  const [condition, setCondition] = useState('')
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [notice, setNotice] = useState('本地草稿模式：变更保存在当前浏览器；服务端 API 待接入。')
  const [dragOver, setDragOver] = useState(false)
  const storageKey = 'agent-platform:workflow-draft:' + (workflowId || 'new')
  useEffect(() => {
    // TODO(#758): Replace browser-local persistence with versioned Definition CRUD + ETag.
    try {
      const raw = window.localStorage.getItem(storageKey)
      if (raw) setDraft(JSON.parse(raw) as WorkflowDraft)
      else setDraft(emptyDraft(workflowId))
      const savedSnapshots = window.localStorage.getItem(storageKey + ':snapshots')
      setSnapshots(savedSnapshots ? JSON.parse(savedSnapshots) as Snapshot[] : [])
    } catch {
      setDraft(emptyDraft(workflowId))
      setNotice('浏览器存储不可用，当前仅使用内存草稿。')
    }
  }, [storageKey, workflowId])
  useEffect(() => {
    try { window.localStorage.setItem(storageKey, JSON.stringify(draft)) }
    catch { setNotice('浏览器存储不可用；当前修改仅保留在页面内存中。') }
  }, [draft, storageKey])
  useEffect(() => {
    // TODO(#758): Persist immutable snapshots through the Workflow version API.
    try { window.localStorage.setItem(storageKey + ':snapshots', JSON.stringify(snapshots)) }
    catch { setNotice('版本快照无法写入浏览器存储；当前页面仍可继续编辑。') }
  }, [snapshots, storageKey])
  const issues = useMemo(() => validate(draft), [draft])
  const selected = draft.nodes.find((node) => node.id === selectedId)
  const snapshotDiff = (snapshot: Snapshot) =>
    (['workflow_id', 'name', 'description', 'nodes', 'edges'] as const).filter(
      (field) =>
        JSON.stringify(snapshot.draft[field]) !== JSON.stringify(draft[field]),
    )

  const updateDraft = (patch: Partial<WorkflowDraft>) =>
    setDraft((current) => ({ ...current, ...patch }))
  const addNode = (type: NodeKind) => {
    const nextIndex = draft.nodes.reduce((max, node) => {
      const match = node.id.match(/-(\d+)$/)
      return match ? Math.max(max, Number(match[1])) : max
    }, 0) + 1
    const node = newNode(type, nextIndex)
    updateDraft({ nodes: [...draft.nodes, node] })
    setSelectedId(node.id)
    setNotice('已添加节点；草稿保存在当前浏览器，尚未写入服务端。')
  }
  const updateNode = (patch: Partial<WorkflowNode>) => {
    if (!selected) return
    updateDraft({
      nodes: draft.nodes.map((node) =>
        node.id === selected.id ? { ...node, ...patch } : node,
      ),
    })
  }
  const addEdge = () => {
    if (!sourceId || !targetId || sourceId === targetId) return
    const id = 'edge-' + (draft.edges.length + 1)
    updateDraft({
      edges: [...draft.edges, { id, source: sourceId, target: targetId, condition }],
    })
    setCondition('')
    setNotice('已添加本地连线；发布前仍需服务端校验端口与 Schema 兼容性。')
  }
  const saveSnapshot = () => {
    setSnapshots((current) => [
      ...current,
      { version: current.length + 1, savedAt: new Date().toISOString(), draft: clone(draft) },
    ])
    setNotice('已创建本地版本快照；可查看字段差异并恢复，服务端版本仍待接入。')
  }
  const restoreSnapshot = (snapshot: Snapshot) => {
    setDraft(clone(snapshot.draft))
    setNotice('已在本地恢复快照 v' + snapshot.version + '；未调用服务端回滚 API。')
  }

  if (!workflowId) {
    return (
      <section className="page-content workflow-designer">
        <div className="workflow-heading">
          <div><p className="eyebrow">ADM-WORKFLOW · ISSUE #758</p><h1>Workflow 定义</h1><p>版本化工作流定义管理入口。后端列表 API 尚未就绪，当前不展示虚构数据。</p></div>
          <Link className="workflow-primary-link" to="/admin/workflows/new/edit">新建 Workflow 草稿</Link>
        </div>
        <EmptyState title="暂无可展示的 Workflow" description="Workflow Definition 列表 API 尚未接入。你可以先创建本地草稿并使用可视化设计器。" />
        <p className="workflow-footer-note">TODO(#758)：接入定义列表、版本摘要、状态过滤、权限控制与分页；列表为空不代表服务端没有定义。</p>
      </section>
    )
  }

  return (
    <section className="page-content workflow-designer">
      <div className="workflow-heading">
        <div>
          <p className="eyebrow">ADM-WORKFLOW · ISSUE #758</p>
          <h1>Workflow 可视化设计器</h1>
          <p>以版本化定义为中心编辑 LangGraph 工作流；本阶段只交付前端原型，不模拟编译或执行。</p>
        </div>
        <div className="workflow-heading-actions">
          <Link className="workflow-link" to="/admin/workflows">Workflow 列表</Link>
          <span className="workflow-mode">本地草稿</span>
        </div>
      </div>

      <div className="workflow-summary">
        <label>Workflow ID<input value={draft.workflow_id} onChange={(event) => updateDraft({ workflow_id: event.target.value })} /></label>
        <label>名称<input value={draft.name} onChange={(event) => updateDraft({ name: event.target.value })} placeholder="例如：research-and-review" /></label>
        <label className="workflow-description">描述<input value={draft.description} onChange={(event) => updateDraft({ description: event.target.value })} placeholder="工作流用途与边界" /></label>
      </div>

      <div className="workflow-toolbar">
        <div><strong>定义编辑器</strong><span>节点 {draft.nodes.length} · 连线 {draft.edges.length} · 本地快照 {snapshots.length}</span></div>
        <div className="workflow-actions">
          <button type="button" onClick={saveSnapshot}>保存本地快照</button>
          <button type="button" disabled>校验并编译（等待 API）</button>
          <button type="button" disabled>发布版本（等待 API）</button>
        </div>
      </div>

      <div className="workflow-workspace">
        <aside className="workflow-palette">
          <h2>节点 Palette</h2>
          <p>拖拽节点到画布，或点击快速添加</p>
          {palette.map((item) => (
            <button type="button" draggable className="workflow-palette-item" key={item.type} onDragStart={(event) => { event.dataTransfer.setData('application/x-workflow-node', item.type); event.dataTransfer.effectAllowed = 'copy' }} onClick={() => addNode(item.type)}>
              <strong>{item.title}</strong><small>{item.detail}</small>
            </button>
          ))}
          <div className="workflow-api-note">TODO(#758)：从后端 Node Registry 读取 node_type、版本、端口及 config_schema；未知节点禁止发布。</div>
        </aside>

        <section className={dragOver ? 'workflow-canvas drag-over' : 'workflow-canvas'} aria-label="Workflow 画布" onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; setDragOver(true) }} onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragOver(false) }} onDrop={(event) => { event.preventDefault(); const kind = event.dataTransfer.getData('application/x-workflow-node') as NodeKind; if (palette.some((item) => item.type === kind)) addNode(kind); setDragOver(false) }}>
          <div className="workflow-canvas-header"><div><h2>流程画布</h2><p>拖拽 Palette 节点至此；节点可选择并配置</p></div><span>LangGraph StateGraph target</span></div>
          {draft.nodes.length === 0 ? (
            <EmptyState title="画布为空" description="从左侧 Palette 添加 Agent、Tool、条件、并行、Join 或审批节点。" />
          ) : (
            <div className="workflow-node-grid">
              {draft.nodes.map((node, index) => (
                <div className="workflow-node-wrap" key={node.id}>
                  <button type="button" className={selectedId === node.id ? 'workflow-node selected' : 'workflow-node'} onClick={() => setSelectedId(node.id)}>
                    <span className="workflow-node-type">{node.node_type} · v{node.version}</span>
                    <strong>{node.name}</strong>
                    <small>{node.id}</small>
                    <span className="workflow-node-risk">风险：{node.risk}</span>
                    <span className="workflow-node-ports">端口：{node.ports.join(', ') || '未配置'}</span>
                  </button>
                  {index < draft.nodes.length - 1 && <span className="workflow-node-arrow" aria-hidden="true">↓</span>}
                </div>
              ))}
            </div>
          )}
          <div className="workflow-edge-editor">
            <strong>连线关系</strong>
            <div className="workflow-edge-form">
              <select aria-label="连线起点" value={sourceId} onChange={(event) => setSourceId(event.target.value)}>
                <option value="">选择起点</option>{draft.nodes.map((node) => <option key={node.id} value={node.id}>{node.name} ({node.id})</option>)}
              </select>
              <select aria-label="连线终点" value={targetId} onChange={(event) => setTargetId(event.target.value)}>
                <option value="">选择终点</option>{draft.nodes.map((node) => <option key={node.id} value={node.id}>{node.name} ({node.id})</option>)}
              </select>
              <input aria-label="条件表达式" value={condition} onChange={(event) => setCondition(event.target.value)} placeholder="条件（可选）" />
              <button type="button" onClick={addEdge} disabled={!sourceId || !targetId || sourceId === targetId}>添加连线</button>
            </div>
            {draft.edges.length ? <ul>{draft.edges.map((edge) => <li key={edge.id}>{edge.source} → {edge.target}{edge.condition ? ' · ' + edge.condition : ''} <button type="button" aria-label={'删除连线 ' + edge.id} onClick={() => updateDraft({ edges: draft.edges.filter((item) => item.id !== edge.id) })}>删除</button></li>)}</ul> : <p className="workflow-muted">尚无连线。</p>}
          </div>
        </section>

        <aside className="workflow-inspector">
          <h2>节点配置</h2>
          {selected ? (
            <div className="workflow-inspector-form">
              <label>节点名称<input value={selected.name} onChange={(event) => updateNode({ name: event.target.value })} /></label>
              <label>node_type<input value={selected.node_type} readOnly /></label>
              <label>节点版本<input type="number" min="1" value={selected.version} onChange={(event) => updateNode({ version: Number(event.target.value) })} /></label>
              <label>风险等级<select value={selected.risk} onChange={(event) => updateNode({ risk: event.target.value as WorkflowNode['risk'] })}><option value="low">low</option><option value="medium">medium</option><option value="high">high</option></select></label>
              <label>权限（逗号分隔）<input value={selected.permissions.join(', ')} onChange={(event) => updateNode({ permissions: event.target.value.split(',').map((value) => value.trim()).filter(Boolean) })} /></label>
              <label>端口（逗号分隔）<input value={selected.ports.join(', ')} onChange={(event) => updateNode({ ports: event.target.value.split(',').map((value) => value.trim()).filter(Boolean) })} /></label>
              <label>Config Schema<textarea rows={5} value={selected.config_schema} onChange={(event) => updateNode({ config_schema: event.target.value })} /></label>
              <label>Input Schema<textarea rows={5} value={selected.input_schema} onChange={(event) => updateNode({ input_schema: event.target.value })} /></label>
              <label>Output Schema<textarea rows={5} value={selected.output_schema} onChange={(event) => updateNode({ output_schema: event.target.value })} /></label>
              <label>失败策略<select value={selected.error_retry.strategy} onChange={(event) => updateNode({ error_retry: { ...selected.error_retry, strategy: event.target.value as 'none' | 'retry' } })}><option value="none">不重试</option><option value="retry">重试</option></select></label>
              <label>最大重试次数<input type="number" min="0" value={selected.error_retry.max_attempts} onChange={(event) => updateNode({ error_retry: { ...selected.error_retry, max_attempts: Number(event.target.value) } })} /></label>
              <button type="button" className="workflow-remove-node" onClick={() => { updateDraft({ nodes: draft.nodes.filter((node) => node.id !== selected.id), edges: draft.edges.filter((edge) => edge.source !== selected.id && edge.target !== selected.id) }); setSelectedId('') }}>移除节点</button>
            </div>
          ) : <EmptyState title="未选择节点" description="选择画布中的节点以编辑 Schema、端口、权限和错误策略。" />}
        </aside>
      </div>

      <div className="workflow-bottom-grid">
        <section className="workflow-validation">
          <h2>本地结构检查 <span>{issues.length ? issues.length + ' 项' : '通过'}</span></h2>
          {issues.length ? <ul>{issues.map((issue) => <li key={issue}>{issue}</li>)}</ul> : <p>当前定义通过基础 JSON/引用检查。此结果不代表后端 schema 兼容或 LangGraph 编译通过。</p>}
          <p className="workflow-muted">TODO(#758)：接入服务端校验诊断、端口类型检查、循环/并行 join 约束、权限风险校验与编译错误定位。</p>
        </section>
        <section className="workflow-versions">
          <h2>草稿快照与版本差异</h2>
          {snapshots.length ? snapshots.map((snapshot) => <div className="workflow-version-row" key={snapshot.version}><div><strong>本地快照 v{snapshot.version}</strong><small>{new Date(snapshot.savedAt).toLocaleString()}</small><small>与当前草稿差异：{snapshotDiff(snapshot).join('、') || '无'}</small></div><button type="button" onClick={() => restoreSnapshot(snapshot)}>恢复到编辑器</button></div>) : <p className="workflow-muted">尚无本地快照。保存快照后可查看字段级差异与恢复；服务端版本列表、持久化 diff、发布与回滚 API 待接入。</p>}
        </section>
      </div>
      <p className="workflow-status" role="status">{notice}</p>
      <div className="workflow-footer-note">架构边界：本设计器只负责定义、校验、版本和发布；运行调度、执行循环、checkpoint、interrupt/resume 始终由 LangGraph 与平台运行时负责。</div>
    </section>
  )
}
