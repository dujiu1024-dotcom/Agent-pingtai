import { useMemo, useState } from 'react'
import { isNodeRuntimeEvent, nodeStatusForRuntimeEvent } from './workflow-run-events'

type RuntimeEvent = Record<string, unknown>
type RuntimeNode = { id: string; label: string; type: string; status: string; firstAt: string; lastAt: string; events: number; input?: unknown; output?: unknown; error?: unknown }
type RuntimeEdge = { id: string; source: string; target: string; label: string }

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
const str = (value: unknown) => typeof value === 'string' && value.trim() ? value : ''
const nodeEventType = (event: RuntimeEvent) => str(event.event_type ?? event.type).toLowerCase()
const nodePayload = (event: RuntimeEvent) => record(event.payload ?? event.data ?? event.summary)
const safeSummary = (value: unknown) => {
  if (value === null || value === undefined) return '暂无摘要'
  const raw = typeof value === 'string' ? value : JSON.stringify(value)
  return raw.length > 180 ? raw.slice(0, 180) + '…' : raw
}

/** 只把事件流中明确存在的节点/关系绘制出来；没有节点事件时不从 Run 状态推断。 */
export function WorkflowRunCanvas({ events }: { events: RuntimeEvent[] }) {
  const [selected, setSelected] = useState('')
  const [view, setView] = useState<'graph' | 'list'>('graph')
  const { nodes, edges } = useMemo(() => {
    const map = new Map<string, RuntimeNode>()
    const links = new Map<string, RuntimeEdge>()
    for (const event of events) {
      const type = nodeEventType(event)
      const payload = nodePayload(event)
      const nodeId = str(payload.node_id ?? payload.nodeId ?? event.node_id)
      if (!isNodeRuntimeEvent(type, nodeId, payload)) continue
      const timestamp = str(event.occurred_at ?? event.created_at)
      const current = map.get(nodeId) ?? {
        id: nodeId,
        label: str(payload.node_name ?? payload.name) || nodeId,
        type: str(payload.node_type ?? payload.kind) || '未知类型',
        status: '暂无节点状态',
        firstAt: timestamp,
        lastAt: timestamp,
        events: 0,
      }
      current.events += 1
      current.firstAt = current.firstAt || timestamp
      current.lastAt = timestamp || current.lastAt
      current.status = nodeStatusForRuntimeEvent(type, payload) || current.status
      if (payload.input !== undefined || payload.input_summary !== undefined) current.input = payload.input_summary ?? payload.input
      if (payload.output !== undefined || payload.output_summary !== undefined) current.output = payload.output_summary ?? payload.output
      if (payload.error !== undefined) current.error = payload.error
      map.set(nodeId, current)
      const parent = str(payload.parent_node_id ?? payload.parentNodeId ?? payload.source_node_id)
      const next = str(payload.next_node_id ?? payload.nextNodeId ?? payload.target_node_id)
      if (parent && nodeId) links.set(parent + '→' + nodeId, { id: parent + '→' + nodeId, source: parent, target: nodeId, label: '事件引用' })
      if (nodeId && next) links.set(nodeId + '→' + next, { id: nodeId + '→' + next, source: nodeId, target: next, label: '事件引用' })
    }
    return { nodes: [...map.values()], edges: [...links.values()] }
  }, [events])
  const active = nodes.find(node => node.id === selected) ?? nodes[0]
  return <section className="workflow-run-canvas" aria-label="Workflow 运行节点画布">
    <header className="workflow-run-canvas-head">
      <div><h3>Workflow 运行态画布</h3><p>节点与关系仅来自本次 Run 的实际事件，不以定义图或父 Run 状态补造遥测</p></div>
      <div className="workflow-run-view-toggle" role="group" aria-label="画布视图">
        <button type="button" aria-pressed={view === 'graph'} onClick={() => setView('graph')}>画布</button>
        <button type="button" aria-pressed={view === 'list'} onClick={() => setView('list')}>列表</button>
      </div>
    </header>
    {nodes.length === 0 ? <p className="workflow-run-no-telemetry" role="status">暂无节点级遥测。当前事件流未包含可识别的 LangGraph 节点事件；不根据 Run 状态推算节点成功、失败或执行路径。</p> : <>
      <div className={view === 'graph' ? 'workflow-run-node-grid graph-view' : 'workflow-run-node-grid list-view'}>
        {nodes.map((node, index) => <div className="workflow-run-node-wrap" key={node.id}>
          <button type="button" className={active?.id === node.id ? 'workflow-run-node selected' : 'workflow-run-node'} onClick={() => setSelected(node.id)} aria-pressed={active?.id === node.id}>
            <small>{node.type} · {node.events} 条事件</small><strong>{node.label}</strong><span className={'workflow-run-node-status ' + (node.status === 'failed' ? 'failed' : node.status === 'completed' || node.status === 'succeeded' ? 'success' : node.status === 'running' ? 'running' : '')}>{node.status}</span>
            <small>{node.lastAt || '事件未提供时间'}</small>
          </button>
          {view === 'graph' && index < nodes.length - 1 && <span className="workflow-run-connector" aria-hidden="true">⋯</span>}
        </div>)}
      </div>
      {edges.length > 0 && <p className="workflow-run-edge-note">事件明确提供的节点关系：{edges.map(edge => edge.id).join(' · ')}</p>}
      {active && <aside className="workflow-run-node-detail" aria-live="polite">
        <h4>{active.label} · {active.id}</h4>
        <dl><div><dt>节点类型</dt><dd>{active.type}</dd></div><div><dt>事件数量</dt><dd>{active.events}</dd></div><div><dt>首次事件</dt><dd>{active.firstAt || '未提供'}</dd></div><div><dt>最近事件</dt><dd>{active.lastAt || '未提供'}</dd></div></dl>
        <p><strong>输入摘要：</strong>{safeSummary(active.input)}</p><p><strong>输出摘要：</strong>{safeSummary(active.output)}</p>
        {active.error !== undefined && <p className="workflow-run-node-error"><strong>错误：</strong>{safeSummary(active.error)}</p>}
      </aside>}
      <p className="workflow-run-legend">状态来自事件 payload；“暂无节点状态”表示事件没有提供可映射的节点状态。</p>
    </>}
  </section>
}
