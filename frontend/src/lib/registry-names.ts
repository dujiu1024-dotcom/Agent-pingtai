/**
 * 注册表条目 ID → 中文名映射的共享工具。
 *
 * 每个 RegistryItem 的 definition 里必有 `name`（创建时填的中文名），
 * 前端所有展示 item_id 的地方都应该先查这个映射，查不到才 fallback 原值。
 */
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/lib/api/api-client'

export interface RegistryNameRow {
  item_id: string
  kind: string
  definition: { name?: string; [key: string]: unknown }
  status: string
}

const ALL_KINDS = ['agent', 'tool', 'skill', 'workflow', 'model_template', 'model_provider', 'routing_policy'] as const
export type RegistryKind = typeof ALL_KINDS[number]

function fetchKind(kind: RegistryKind) {
  return apiClient.get<{ items: RegistryNameRow[] }>(
    `/platform/v1/registry/${kind}`,
  )
}

/** 缓存所有常用 kind 的 registry 列表，供映射使用。 */
export function useRegistryCaches() {
  const toolQ = useQuery({ queryKey: ['registry', 'tool'], queryFn: () => fetchKind('tool'), retry: false })
  const skillQ = useQuery({ queryKey: ['registry', 'skill'], queryFn: () => fetchKind('skill'), retry: false })
  const workflowQ = useQuery({ queryKey: ['registry', 'workflow'], queryFn: () => fetchKind('workflow'), retry: false })
  const agentQ = useQuery({ queryKey: ['registry', 'agent'], queryFn: () => fetchKind('agent'), retry: false })
  const modelQ = useQuery({ queryKey: ['registry', 'model_template'], queryFn: () => fetchKind('model_template'), retry: false })
  const providerQ = useQuery({ queryKey: ['registry', 'model_provider'], queryFn: () => fetchKind('model_provider'), retry: false })

  return {
    toolNames: useMemo(() => buildNameMap(toolQ.data?.items), [toolQ.data]),
    skillNames: useMemo(() => buildNameMap(skillQ.data?.items), [skillQ.data]),
    workflowNames: useMemo(() => buildNameMap(workflowQ.data?.items), [workflowQ.data]),
    agentNames: useMemo(() => buildNameMap(agentQ.data?.items), [agentQ.data]),
    modelTemplateNames: useMemo(() => buildNameMap(modelQ.data?.items), [modelQ.data]),
    modelProviderNames: useMemo(() => buildNameMap(providerQ.data?.items), [providerQ.data]),
  }
}

/** 把 registry items 数组转成 { [item_id]: definition.name } 映射表。 */
export function buildNameMap(items: RegistryNameRow[] | undefined | null): Record<string, string> {
  const map: Record<string, string> = {}
  if (!items) return map
  for (const item of items) {
    const name = String(item.definition?.name ?? '').trim()
    map[item.item_id] = name || item.item_id
  }
  return map
}

/**
 * 查找一个 item_id 的中文名；查不到返回 fallback（默认就是原值）。
 * 用法：`resolveName(toolNames, 'web_search')` → `'网页搜索'`
 */
export function resolveName(
  map: Record<string, string>,
  id: string | undefined | null,
): string {
  if (!id) return '—'
  return map[id] ?? id
}

/**
 * 把 id 列表一次性映射成中文名字列表，适合 badge、逗号分隔展示。
 * 用法：`resolveNames(toolNames, ids)` → `['网页搜索', '计算器']`
 */
export function resolveNames(
  map: Record<string, string>,
  ids: unknown[] | undefined | null,
): string[] {
  if (!Array.isArray(ids)) return []
  return ids.map((id) => resolveName(map, String(id)))
}
