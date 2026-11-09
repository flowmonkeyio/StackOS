import type { SchemaUsageSampleOut } from '@/api'

export type LiveMetric = 'input' | 'cache_read' | 'output'
export function sampleValue(sample: SchemaUsageSampleOut, metric: LiveMetric): number | null {
  if (metric === 'input') return sample.remaining_input ?? (sample.cache_read == null ? null : sample.input - sample.cache_read)
  return sample[metric] ?? null
}
export interface UsageTraceGeometry {
  state: 'empty' | 'point' | 'bars'
  buckets: { at: number; value: number | null }[]
  maximum: number
  startAt: number | null
  endAt: number | null
  baselineAt: number | null
}

const MINUTE = 60_000
export const DEFAULT_USAGE_BAR_COUNT = 20

// The counters are cumulative. Each difference belongs to the minute in which
// it was reported, not an inferred time of consumption. Omit endAt to retain a
// completed chart; a shared feed clock can advance active charts without timers.
export function usageTrace(samples: SchemaUsageSampleOut[], metric: LiveMetric, endAt?: number, barCount = DEFAULT_USAGE_BAR_COUNT): UsageTraceGeometry {
  const count = Number.isFinite(barCount) ? Math.min(60, Math.max(5, Math.floor(barCount))) : DEFAULT_USAGE_BAR_COUNT
  const latestAt = samples.reduce((latest, sample) => {
    const at = Date.parse(sample.at)
    return Number.isFinite(at) ? Math.max(latest, at) : latest
  }, -Infinity)
  const anchor = endAt != null && Number.isFinite(endAt) ? endAt : latestAt
  const windowEnd = Number.isFinite(anchor) ? (Math.floor(anchor / MINUTE) + 1) * MINUTE : null
  const windowStart = windowEnd == null ? null : windowEnd - count * MINUTE
  const buckets = Array.from({ length: count }, (_, index) => ({ at: (windowStart ?? 0) + index * MINUTE, value: null as number | null }))
  const gaps = new Set<number>()
  let previous: { at: number; value: number } | null = null
  let baselineAt: number | null = null

  for (const sample of samples) {
    const at = Date.parse(sample.at)
    if (Number.isFinite(at) && at > anchor) continue
    const value = sampleValue(sample, metric)
    const index = windowStart == null ? -1 : Math.floor((at - windowStart) / MINUTE)
    const bucket = buckets[index]
    const valid = Number.isFinite(at) && value != null && Number.isFinite(value) && value >= 0
    if (!valid) {
      if (bucket) { bucket.value = null; gaps.add(index) }
      previous = null
      baselineAt = null
      continue
    }
    if (previous) {
      const difference = value - previous.value
      if (at <= previous.at || difference < 0) {
        if (bucket) { bucket.value = null; gaps.add(index) }
      } else if (bucket && !gaps.has(index)) {
        bucket.value = (bucket.value ?? 0) + difference
      }
    }
    previous = { at, value }
    baselineAt = bucket ? at : null
  }

  const measured = buckets.some(bucket => bucket.value != null)
  return {
    state: measured ? 'bars' : baselineAt == null ? 'empty' : 'point',
    buckets,
    maximum: Math.max(0, ...buckets.map(bucket => bucket.value ?? 0)),
    startAt: windowStart,
    endAt: windowEnd,
    baselineAt,
  }
}

export function usageTraceDescription(geometry: UsageTraceGeometry, metric: LiveMetric): string {
  const label = metric === 'cache_read' ? 'Cache' : metric === 'input' ? 'Input' : 'Output'
  const introduction = `${label}: newly reported tokens in ${geometry.buckets.length} one-minute intervals. Each chart uses its own zero-based scale.`
  if (geometry.state === 'empty') return `${introduction} No measured changes in this window.`
  if (geometry.state === 'point') return `${introduction} First reading establishes a baseline; no increase inferred.`
  const time = (at: number) => new Date(at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  return `${introduction} ${geometry.buckets.map(bucket => `${time(bucket.at)}–${time(bucket.at + MINUTE)}: ${bucket.value == null ? 'unavailable' : `${bucket.value.toLocaleString('en-US')} tokens`}`).join('; ')}.`
}
