import { useMemo, useState } from 'react'

export type WorkflowNode = {
  id: string; name: string; node_type: string; version: number
  config: Record<string, unknown>; config_schema: Record<string, unknown>
  input_schema: Record<string, unknown>; output_schema: Record<string, unknown>
  ports: { inputs: string[]; outputs: string[] }; permissions: string[]
  risk: 'low' | 'medium' | 'high'
  error_retry: { max_attempts: number; backoff: 'none' | 'linear' | 'exponential' }
}
export type WorkflowEdge = { id: string; source: string; target: string; source_port: string; target_port: string; condition?: string }

const TYPES = [
  { type: 'start', label: '开始', inputs: [], outputs: ['main'] },
  { type: 'agent', label: 'Agent', inputs: ['main'], outputs: ['main', 'error'] },
  { type: 'tool', label: 'Tool / MCP', inputs: ['main'], outputs: ['main', 'error'] },
  { type: 'condition', label: '条件分支', inputs: ['main'], outputs: ['true', 'false'] },
  { type: 'parallel', label: '并行分发', inputs: ['main'], outputs: ['branch-a', 'branch-b'] },
  { type: 'join', label: '并行汇合', inputs: ['branch-a', 'branch-b'], outputs: ['main'] },
  { type: 'approval', label: '人工审批', inputs: ['main'], outputs: ['approved', 'rejected'] },
  { type: 'end', label: '结束', inputs: ['main'], outputs: [] },
] as const

function validate(nodes: WorkflowNode[], edges: WorkflowEdge[]) {
  const errors: string[] = []
  const ids = new Set<string>()
  nodes.forEach((node) => {
    if (!node.id.trim() || ids.has(node.id)) errors.push(`节点 ID 为空或重复：${node.id}`)
    ids.add(node.id)
    if (!TYPES.some((type) => type.type === node.node_type)) errors.push(`未知节点类型：${node.node_type}`)
    if (!Number.isInteger(node.version) || node.version < 1) errors.push(`节点 ${node.id} 版本无效`)
    if (!node.config_schema || !node.input_schema || !node.output_schema || !node.ports) errors.push(`节点 ${node.id} 缺少 Schema / ports 契约`)
    if (!node.error_retry || node.error_retry.max_attempts < 1) errors.push(`节点 ${node.id} 重试策略无效`)
  })
  if (nodes.filter((node) => node.node_type === 'start').length !== 1) errors.push('必须且只能有一个开始节点')
  if (!nodes.some((node) => node.node_type === 'end')) errors.push('至少需要一个结束节点')
  edges.forEach((edge) => {
    const from = nodes.find((node) => node.id === edge.source)
    const to = nodes.find((node) => node.id === edge.target)
    if (!from || !to) errors.push(`连线 ${edge.id} 引用了不存在的节点`)
    else {
      if (!from.ports.outputs.includes(edge.source_port)) errors.push(`连线 ${edge.id} 源端口无效`)
      if (!to.ports.inputs.includes(edge.target_port)) errors.push(`连线 ${edge.id} 目标端口无效`)
      if (from.id === to.id) errors.push(`连线 ${edge.id} 不允许自环`)
    }
  })
  nodes.forEach((node) => {
    if (node.node_type !== 'start' && !edges.some((edge) => edge.target === node.id)) errors.push(`节点 ${node.id} 缺少输入连线`)
    if (node.node_type !== 'end' && !edges.some((edge) => edge.source === node.id)) errors.push(`节点 ${node.id} 缺少输出连线`)
  })
  return errors
}

export function WorkflowGraphEditor({ nodes, edges, onNodesChange, onEdgesChange }: {
  nodes: WorkflowNode[]; edges: WorkflowEdge[]
  onNodesChange: (nodes: WorkflowNode[]) => void; onEdgesChange: (edges: WorkflowEdge[]) => void
}) {
  const [selectedId, setSelectedId] = useState(nodes[0]?.id ?? '')
  const [source, setSource] = useState(''); const [target, setTarget] = useState('')
  const [sourcePort, setSourcePort] = useState('main'); const [targetPort, setTargetPort] = useState('main')
  const errors = useMemo(() => validate(nodes, edges), [nodes, edges])
  const selected = nodes.find((node) => node.id === selectedId)
  const update = (patch: Partial<WorkflowNode>) => selected && onNodesChange(nodes.map((node) => node.id === selected.id ? { ...node, ...patch } : node))
  const add = (type: (typeof TYPES)[number]) => {
    let index = nodes.length + 1; let id = `${type.type}-${index}`
    while (nodes.some((node) => node.id === id)) id = `${type.type}-${++index}`
    const node: WorkflowNode = {
      id, name: type.label, node_type: type.type, version: 1, config: {},
      config_schema: { type: 'object', properties: {} }, input_schema: { type: 'object' }, output_schema: { type: 'object' },
      ports: { inputs: [...type.inputs], outputs: [...type.outputs] }, permissions: [],
      risk: type.type === 'approval' ? 'high' : type.type === 'tool' ? 'medium' : 'low',
      error_retry: { max_attempts: 1, backoff: 'none' },
    }
    onNodesChange([...nodes, node]); setSelectedId(id)
  }
  const addEdge = () => {
    if (!source || !target) return
    onEdgesChange([...edges, { id: `edge-${edges.length + 1}`, source, target, source_port: sourcePort, target_port: targetPort }])
  }
  return <section className="workflow-visual-editor">
    <aside className="workflow-node-palette"><h2>节点 Palette</h2>{TYPES.map((type) => <button key={type.type} type="button" onClick={() => add(type)}><strong>{type.label}</strong><small>{type.type} · v1</small></button>)}</aside>
    <div className="workflow-graph-main"><header><strong>StateGraph 画布</strong><span>{nodes.length} 节点 · {edges.length} 连线</span></header>
      <div className="workflow-graph-canvas">{nodes.length === 0 && <p>从 Palette 添加节点</p>}{nodes.map((node) => <button key={node.id} type="button" className={selectedId === node.id ? 'workflow-graph-node selected' : 'workflow-graph-node'} onClick={() => setSelectedId(node.id)}><small>{node.node_type} · v{node.version}</small><strong>{node.name || node.id}</strong><span>输入：{node.ports.inputs.join(', ') || '无'}</span><span>输出：{node.ports.outputs.join(', ') || '无'}</span><em>风险：{node.risk}</em></button>)}</div>
      <h3>连线 / 条件分支 / 并行与 Join</h3><div className="workflow-edge-builder">
        <select aria-label="源节点" value={source} onChange={(event) => setSource(event.target.value)}><option value="">源节点</option>{nodes.map((node) => <option key={node.id} value={node.id}>{node.id}</option>)}</select>
        <select aria-label="源端口" value={sourcePort} onChange={(event) => setSourcePort(event.target.value)}>{(nodes.find((node) => node.id === source)?.ports.outputs ?? ['main']).map((port) => <option key={port}>{port}</option>)}</select>
        <select aria-label="目标节点" value={target} onChange={(event) => setTarget(event.target.value)}><option value="">目标节点</option>{nodes.map((node) => <option key={node.id} value={node.id}>{node.id}</option>)}</select>
        <select aria-label="目标端口" value={targetPort} onChange={(event) => setTargetPort(event.target.value)}>{(nodes.find((node) => node.id === target)?.ports.inputs ?? ['main']).map((port) => <option key={port}>{port}</option>)}</select>
        <button type="button" disabled={!source || !target} onClick={addEdge}>添加连线</button>
      </div><ul className="workflow-edge-list">{edges.map((edge) => <li key={edge.id}>{edge.source}.{edge.source_port} → {edge.target}.{edge.target_port}<button type="button" onClick={() => onEdgesChange(edges.filter((item) => item.id !== edge.id))}>删除</button></li>)}</ul>
    </div>
    <aside className="workflow-node-inspector"><h2>节点配置</h2>{!selected && <p>选择节点编辑其契约。</p>}{selected && <div className="workflow-node-fields">
      <label>ID<input value={selected.id} onChange={(event) => update({ id: event.target.value })}/></label>
      <label>名称<input value={selected.name} onChange={(event) => update({ name: event.target.value })}/></label>
      <label>节点类型<select value={selected.node_type} onChange={(event) => { const type = TYPES.find((item) => item.type === event.target.value); if (type) update({ node_type: type.type, ports: { inputs: [...type.inputs], outputs: [...type.outputs] } }) }}>{TYPES.map((type) => <option key={type.type} value={type.type}>{type.label}</option>)}</select></label>
      <label>版本<input type="number" min="1" value={selected.version} onChange={(event) => update({ version: Number(event.target.value) })}/></label>
      <label>风险<select value={selected.risk} onChange={(event) => update({ risk: event.target.value as WorkflowNode['risk'] })}><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select></label>
      <label>权限（逗号分隔）<input value={selected.permissions.join(',')} onChange={(event) => update({ permissions: event.target.value.split(',').map((item) => item.trim()).filter(Boolean) })}/></label>
      <label>最大重试次数<input type="number" min="1" value={selected.error_retry.max_attempts} onChange={(event) => update({ error_retry: { ...selected.error_retry, max_attempts: Number(event.target.value) } })}/></label>
      <label>退避策略<select value={selected.error_retry.backoff} onChange={(event) => update({ error_retry: { ...selected.error_retry, backoff: event.target.value as WorkflowNode['error_retry']['backoff'] })}><option value="none">无</option><option value="linear">线性</option><option value="exponential">指数</option></select></label>
      {(['config', 'config_schema', 'input_schema', 'output_schema'] as const).map((field) => <label key={field}>{field}<textarea rows={4} value={JSON.stringify(selected[field], null, 2)} onChange={(event) => { try { update({ [field]: JSON.parse(event.target.value) }) } catch { /* keep last valid JSON */ } }}/></label>)}
      <button type="button" onClick={() => { onNodesChange(nodes.filter((node) => node.id !== selected.id)); onEdgesChange(edges.filter((edge) => edge.source !== selected.id && edge.target !== selected.id)); setSelectedId('') }}>删除节点及连线</button>
    </div>}</aside>
    <div className="workflow-graph-validation"><strong>本地结构校验</strong><span>{errors.length ? `${errors.length} 项问题` : '结构检查通过；不代表服务端 Schema 校验或 LangGraph 编译通过'}</span>{errors.length > 0 && <ul>{errors.map((error) => <li key={error}>{error}</li>)}</ul>}</div>
  </section>
}

import './workflow-graph-editor.css'
