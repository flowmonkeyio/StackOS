import type { SchemaUsageSampleOut } from '@/api'

export type LiveMetric = 'input' | 'cache_read' | 'output'
export function sampleValue(sample: SchemaUsageSampleOut, metric: LiveMetric): number | null {
  if (metric === 'input') return sample.remaining_input ?? (sample.cache_read == null ? null : sample.input - sample.cache_read)
  return sample[metric] ?? null
}
export interface UsageTraceGeometry {
  state: 'empty' | 'point' | 'trace'
  points: { x: number; y: number }[]
}

// Geometry uses a 28 × 12 coordinate space; the canvas owns pixel scaling.
export function usageTrace(samples: SchemaUsageSampleOut[], metric: LiveMetric): UsageTraceGeometry {
  // Missing counters break a trace. Never prefix zero or bridge absent evidence.
  let lastMissing = -1
  samples.forEach((sample, i) => { if (sampleValue(sample, metric) == null || !Number.isFinite(Date.parse(sample.at))) lastMissing = i })
  const points = samples.slice(lastMissing + 1)
  if (!points.length) return { state: 'empty', points: [] }
  if (points.length === 1) return { state: 'point', points: [{ x: 14, y: 6 }] }
  const values = points.map((point) => sampleValue(point, metric)!)
  const minimum = Math.min(...values), maximum = Math.max(...values)
  const start = Date.parse(points[0]!.at), end = Date.parse(points.at(-1)!.at)
  if (!(end > start)) return { state: 'point', points: [{ x: 14, y: 6 }] }
  const y = (value: number) => maximum === minimum ? 6 : 11 - (value - minimum) / (maximum - minimum) * 10
  return { state: 'trace', points: points.map((point, i) => ({
    x: 1 + (Date.parse(point.at) - start) / (end - start) * 26,
    y: y(values[i]!),
  })) }
}
