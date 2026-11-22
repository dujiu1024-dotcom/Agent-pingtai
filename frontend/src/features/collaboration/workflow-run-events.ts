export type RuntimeEventRecord = Record<string, unknown>

const record = (value: unknown): RuntimeEventRecord =>
  value && typeof value === 'object' && !Array.isArray(value) ? value as RuntimeEventRecord : {}

/** Convert the real Run broker SSE envelope into the durable timeline event shape. */
export function normalizeRunSseEvent(
  raw: unknown,
  eventId: string,
  occurredAt = new Date().toISOString(),
): RuntimeEventRecord | null {
  const data = record(raw)
  const type = typeof data.event_type === 'string' ? data.event_type : ''
  if (!type || type === 'RECONNECT_REQUIRED') return null
  const sequence = typeof data.sequence === 'number' && Number.isFinite(data.sequence) ? data.sequence : undefined
  return {
    event_id: eventId || (sequence !== undefined && typeof data.run_id === 'string' ? `${data.run_id}:${sequence}` : ''),
    event_type: type,
    type,
    sequence,
    occurred_at: typeof data.occurred_at === 'string' ? data.occurred_at : occurredAt,
    node_id: typeof data.node_id === 'string' ? data.node_id : undefined,
    agent_id: typeof data.agent_id === 'string' ? data.agent_id : undefined,
    payload: data.payload ?? data,
  }
}

/** Snapshot and live events can overlap during reconnect; preserve one record per broker cursor. */
export function mergeRunRuntimeEvents(
  snapshot: RuntimeEventRecord[],
  live: RuntimeEventRecord[],
): RuntimeEventRecord[] {
  const merged = new Map<string, RuntimeEventRecord>()
  for (const event of [...snapshot, ...live]) {
    const id = typeof event.event_id === 'string' && event.event_id
      ? event.event_id
      : typeof event.sequence === 'number' ? `sequence:${event.sequence}` : ''
    const key = id || `fallback:${String(event.event_type ?? event.type ?? '')}:${String(event.occurred_at ?? '')}`
    merged.set(key, { ...merged.get(key), ...event })
  }
  return [...merged.values()].sort((a, b) => {
    const left = typeof a.sequence === 'number' ? a.sequence : Number.MAX_SAFE_INTEGER
    const right = typeof b.sequence === 'number' ? b.sequence : Number.MAX_SAFE_INTEGER
    return left - right || String(a.occurred_at ?? '').localeCompare(String(b.occurred_at ?? ''))
  })
}

export function isNodeRuntimeEvent(type: string, nodeId: string, payload: RuntimeEventRecord): boolean {
  const normalized = type.toLowerCase()
  return Boolean(nodeId) && (
    normalized.includes('node.') ||
    normalized.startsWith('langgraph.node') ||
    normalized.startsWith('step_') ||
    normalized.startsWith('step.') ||
    Boolean(payload.node_type || payload.node_name || payload.nodeType || payload.nodeName)
  )
}

export function nodeStatusForRuntimeEvent(type: string, payload: RuntimeEventRecord): string {
  const explicit = payload.node_status ?? payload.status ?? payload.to_status
  if (typeof explicit === 'string' && explicit.trim()) return explicit
  const normalized = type.toLowerCase().replace(/[.]/g, '_')
  if (/(node|step)_started$/.test(normalized) || normalized === 'step_start') return 'running'
  if (/(node|step)_(finished|completed)$/.test(normalized) || normalized === 'step_finish') return 'completed'
  if (/(node|step)_(failed|error)$/.test(normalized) || normalized === 'step_error') return 'failed'
  if (/(node|step)_interrupted$/.test(normalized)) return 'interrupted'
  if (/(node|step)_retry(ing)?$/.test(normalized)) return 'retrying'
  return ''
}
