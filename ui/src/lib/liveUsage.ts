import type { ActiveExecution } from '@/lib/task-tracker/types'
import type { SchemaUsageSampleOut } from '@/api'

export type LiveMetric = 'input' | 'cache_read' | 'output'
export function sampleValue(sample: SchemaUsageSampleOut, metric: LiveMetric): number | null {
  if (metric === 'input') return sample.remaining_input ?? (sample.cache_read == null ? null : sample.input - sample.cache_read)
  return sample[metric] ?? null
}

export function stepTrace(samples: SchemaUsageSampleOut[], metric: LiveMetric): string {
  // Missing counters break a trace. Never prefix zero or bridge absent evidence.
  let lastMissing = -1
  samples.forEach((sample, i) => { if (sampleValue(sample, metric) == null) lastMissing = i })
  const points = samples.slice(lastMissing + 1)
  if (points.length < 2) return ''
  const values = points.map((point) => sampleValue(point, metric)!)
  const minimum = Math.min(...values), maximum = Math.max(...values)
  const start = Date.parse(points[0]!.at), end = Date.parse(points.at(-1)!.at)
  if (!(end > start)) return ''
  const y = (value: number) => maximum === minimum ? 6 : 11 - (value - minimum) / (maximum - minimum) * 10
  return points.map((point, i) => {
    const x = 1 + (Date.parse(point.at) - start) / (end - start) * 26
    return `${i ? 'H' : 'M'}${x.toFixed(2)}${i ? 'V' : ','}${y(values[i]!).toFixed(2)}`
  }).join(' ')
}

export function liveUsageTitle(execution?: ActiveExecution | null): string {
  if (!execution) return ''
  const freshness = execution.live_usage
    ? `Updated ${new Date(execution.live_usage.measured_at).toLocaleString()}.`
    : 'No measured history yet.'
  return `Totals include all recorded calls. Charts show only ${execution.agent}, started ${new Date(execution.started_at).toLocaleString()}, recent measured history (up to 10 minutes). ${freshness}`
}
