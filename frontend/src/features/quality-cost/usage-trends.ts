export type UsageTrendEvent = {
  occurred_at: string
  input_tokens?: number | null
  output_tokens?: number | null
  total_tokens?: number | null
  cost_usd?: string | number | null
}

export type DailyUsageTrend = {
  date: string
  inputTokens: number
  outputTokens: number
  totalTokens: number
  costUsd: number | null
  pricedEvents: number
  unpricedEvents: number
}

/** Aggregate only fields actually returned by the usage ledger; missing prices stay missing. */
export function buildDailyUsageTrend(events: UsageTrendEvent[]): DailyUsageTrend[] {
  const days = new Map<string, DailyUsageTrend>()
  for (const event of events) {
    const timestamp = Date.parse(event.occurred_at)
    if (!Number.isFinite(timestamp)) continue
    const date = new Date(timestamp).toISOString().slice(0, 10)
    let day = days.get(date)
    if (!day) {
      day = { date, inputTokens: 0, outputTokens: 0, totalTokens: 0, costUsd: null, pricedEvents: 0, unpricedEvents: 0 }
      days.set(date, day)
    }
    const input = Number(event.input_tokens ?? 0)
    const output = Number(event.output_tokens ?? 0)
    const total = Number(event.total_tokens ?? (input + output))
    day.inputTokens += Number.isFinite(input) ? input : 0
    day.outputTokens += Number.isFinite(output) ? output : 0
    day.totalTokens += Number.isFinite(total) ? total : 0
    const rawCost = event.cost_usd
    if (rawCost == null || rawCost === '') {
      day.unpricedEvents += 1
      continue
    }
    const cost = Number(rawCost)
    if (!Number.isFinite(cost)) {
      day.unpricedEvents += 1
      continue
    }
    day.costUsd = (day.costUsd ?? 0) + cost
    day.pricedEvents += 1
  }
  return [...days.values()].sort((a, b) => a.date.localeCompare(b.date))
}
