export type BlackboardMessageInput = {
  scope: string
  scopeId: string
  title: string
  message: string
}

const ALLOWED_SCOPES = new Set(['run', 'task', 'project', 'tenant'])

/** Build the existing Blackboard Entry API payload; actor and tenant are server-derived. */
export function createBlackboardMessagePayload(input: BlackboardMessageInput) {
  const scope = input.scope.trim()
  const scopeId = input.scopeId.trim()
  const message = input.message.trim()
  if (!ALLOWED_SCOPES.has(scope)) throw new Error('请选择有效的 Blackboard 范围。')
  if (!scopeId) throw new Error('请填写 Blackboard 范围标识。')
  if (!message) throw new Error('消息内容不能为空。')
  return {
    scope,
    scope_id: scopeId,
    kind: 'note',
    content: { title: input.title.trim() || '协作消息', message },
    source_refs: [],
  }
}
