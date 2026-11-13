import type { SchemaUsageIntervalOut, SchemaUsageSampleOut } from '@/api'

export type LiveMetric = 'input' | 'cache_read' | 'output'
export function sampleValue(sample: SchemaUsageSampleOut | SchemaUsageIntervalOut, metric: LiveMetric): number | null {
  if (metric === 'input') return sample.remaining_input ?? (sample.cache_read == null || sample.input == null ? null : sample.input - sample.cache_read)
  return sample[metric] ?? null
}
export interface UsageTraceGeometry {
  state: 'empty' | 'point' | 'bars' | 'batched'
  buckets: { at: number; value: number | null; observations: { kind: string; at: number; startAt: number | null; value: number | null }[] }[]
  maximum: number
  startAt: number | null
  endAt: number | null
  baselineAt: number | null
}

const MINUTE = 60_000
export const DEFAULT_USAGE_BAR_COUNT = 20

// The producer pairs each invocation before combining lifetime counters. First
// retained totals and gaps cannot become bars when another invocation joins.
// Source checkpoint increments use their source minute; batches get a neutral
// marker with their amount/bounds, never a height or an invented distribution.
export function usageTrace(samples: SchemaUsageSampleOut[], metric: LiveMetric, endAt?: number, barCount = DEFAULT_USAGE_BAR_COUNT): UsageTraceGeometry {
  const count = Number.isFinite(barCount) ? Math.min(60, Math.max(5, Math.floor(barCount))) : DEFAULT_USAGE_BAR_COUNT
  const latestAt = samples.reduce((latest, sample) => {
    const at = Date.parse(sample.at)
    return Number.isFinite(at) ? Math.max(latest, at) : latest
  }, -Infinity)
  const anchor = endAt != null && Number.isFinite(endAt) ? endAt : latestAt
  const windowEnd = Number.isFinite(anchor) ? (Math.floor(anchor / MINUTE) + 1) * MINUTE : null
  const windowStart = windowEnd == null ? null : windowEnd - count * MINUTE
  const buckets: UsageTraceGeometry['buckets'] = Array.from({ length: count }, (_, index) => ({ at: (windowStart ?? 0) + index * MINUTE, value: null, observations: [] }))
  const gaps = new Set<number>()
  let previous: { at: number; value: number } | null = null
  let baselineAt: number | null = null

  const ordered = [...samples].sort((a, b) => Date.parse(a.at) - Date.parse(b.at))
  let lastSample: SchemaUsageSampleOut | null = null
  for (const sample of ordered) {
    const at = Date.parse(sample.at)
    if (Number.isFinite(at) && at > anchor) continue
    const value = sampleValue(sample, metric)
    const index = windowStart == null ? -1 : Math.floor((at - windowStart) / MINUTE)
    const bucket = buckets[index]
    const valid = Number.isFinite(at) && value != null && Number.isFinite(value) && value >= 0
    if (lastSample && at === Date.parse(lastSample.at)) {
      const priorIntervals = lastSample.intervals ?? []
      const sameIntervals = (sample.intervals ?? []).length === priorIntervals.length
        && (sample.intervals ?? []).every((interval, i) => {
          const prior = priorIntervals[i]!
          return interval.kind === prior.kind && interval.start_at === prior.start_at && sampleValue(interval, metric) === sampleValue(prior, metric)
        })
      if (value === sampleValue(lastSample, metric) && sameIntervals) continue
      if (bucket) { bucket.value = null; gaps.add(index) }
      previous = null
      continue
    }
    lastSample = sample
    if (!valid) {
      if (bucket) { bucket.value = null; gaps.add(index) }
      previous = null
      baselineAt = null
      continue
    }
    if (sample.intervals != null) {
      for (const interval of sample.intervals) {
        const change = sampleValue(interval, metric)
        if (interval.kind === 'checkpoint') {
          if (bucket && change != null && change >= 0 && !gaps.has(index)) bucket.value = (bucket.value ?? 0) + change
        } else if (bucket) {
          bucket.observations.push({ kind: interval.kind, at, startAt: interval.start_at ? Date.parse(interval.start_at) : null, value: change })
          if (interval.kind === 'reset') { bucket.value = null; gaps.add(index) }
        }
      }
    } else if (previous) {
      const difference = value - previous.value
      if (at <= previous.at || difference < 0) {
        if (bucket) { bucket.value = null; gaps.add(index) }
      } else if (bucket && at - previous.at > MINUTE) {
        bucket.observations.push({ kind: 'batched', at, startAt: previous.at, value: difference })
      } else if (bucket && !gaps.has(index)) {
        bucket.value = (bucket.value ?? 0) + difference
      }
    } else if (bucket) {
      bucket.observations.push({ kind: 'baseline', at, startAt: null, value })
    }
    previous = { at, value }
    baselineAt = bucket ? at : null
  }

  const measured = buckets.some(bucket => bucket.value != null)
  return {
    state: measured ? 'bars' : buckets.some(bucket => bucket.observations.some(o => o.kind === 'batched')) ? 'batched' : buckets.some(bucket => bucket.observations.some(o => o.kind === 'baseline')) ? 'point' : 'empty',
    buckets,
    maximum: Math.max(0, ...buckets.map(bucket => bucket.value ?? 0)),
    startAt: windowStart,
    endAt: windowEnd,
    baselineAt,
  }
}

export function usageTraceDescription(geometry: UsageTraceGeometry, metric: LiveMetric): string {
  const label = metric === 'cache_read' ? 'Cache' : metric === 'input' ? 'Input' : 'Output'
  const introduction = `${label}: source checkpoint increments in ${geometry.buckets.length} one-minute intervals. Each chart uses its own zero-based scale. Neutral ticks mark baselines or batched observations, not consumption spikes.`
  if (geometry.state === 'empty' && geometry.buckets.every(bucket => !bucket.observations.length)) return `${introduction} No measured changes in this window.`
  const baseline = geometry.state === 'point' ? ' First reading establishes a baseline; no increase inferred.' : ''
  const time = (at: number) => new Date(at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  const instant = (at: number) => new Date(at).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit' })
  return `${introduction}${baseline} ${geometry.buckets.map(bucket => {
    const observations = bucket.observations.map(o => `${o.kind === 'baseline' ? 'baseline' : o.kind === 'reset' ? 'counter reset' : 'batched observation'} at ${instant(o.at)}${o.startAt == null ? '' : ` since ${instant(o.startAt)}`}: ${o.value == null ? 'unavailable' : `${o.value.toLocaleString('en-US')} tokens`}; consumption time unavailable`).join(', ')
    return `${time(bucket.at)}–${time(bucket.at + MINUTE)}: ${bucket.value == null ? 'unavailable' : `${bucket.value.toLocaleString('en-US')} tokens`}${observations ? `; ${observations}` : ''}`
  }).join('; ')}.`
}
