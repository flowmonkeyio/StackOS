import { describe, expect, it } from 'vitest'
import { DEFAULT_USAGE_BAR_COUNT, sampleValue, usageTrace, usageTraceDescription } from './liveUsage'
import type { SchemaUsageSampleOut } from '@/api'

const start = Date.parse('2026-11-09T00:00:00Z')
const sample = (seconds: number, input: number, cache: number | null = 0, output = 0): SchemaUsageSampleOut => ({
  at: new Date(start + seconds * 1000).toISOString(), input, cache_read: cache, output,
  total: input + output, cache_write: null, reasoning: null, remaining_input: null,
})
const values = (samples: SchemaUsageSampleOut[], metric: 'input' | 'cache_read' | 'output' = 'input', endAt?: number) => usageTrace(samples, metric, endAt).buckets.map(bucket => bucket.value)

describe('reported activity bars', () => {
  it('reserves twenty unknown intervals and treats the first retained total only as a baseline', () => {
    const empty = usageTrace([], 'input')
    expect(empty.state).toBe('empty')
    expect(empty.buckets).toHaveLength(DEFAULT_USAGE_BAR_COUNT)
    expect(empty.buckets.every(bucket => bucket.value === null)).toBe(true)
    const first = usageTrace([sample(0, 42_100)], 'input')
    expect(first.state).toBe('point')
    expect(first.baselineAt).toBe(start)
    expect(first.maximum).toBe(0)
    expect(first.buckets.every(bucket => bucket.value === null)).toBe(true)
    expect(usageTraceDescription(first, 'input')).toContain('no increase inferred')
  })

  it('sums differences in the later reporting minute rather than plotting cumulative heights', () => {
    const samples = [sample(0, 1000), sample(15, 1010), sample(50, 1030), sample(80, 1035)]
    const geometry = usageTrace(samples, 'input')
    expect(geometry.state).toBe('bars')
    expect(geometry.buckets.slice(-2).map(bucket => bucket.value)).toEqual([30, 5])
    expect(geometry.maximum).toBe(30)
    expect(geometry.buckets.slice(0, -2).every(bucket => bucket.value === null)).toBe(true)
    expect(usageTraceDescription(geometry, 'input')).toContain('30 tokens')
  })

  it('uses paired Input and separate zero-based scales for each metric', () => {
    const samples = [sample(0, 100, 80, 5), sample(65, 250, 200, 9)]
    expect(sampleValue(samples[0]!, 'input')).toBe(20)
    expect(usageTrace(samples, 'input').maximum).toBe(30)
    expect(usageTrace(samples, 'cache_read').maximum).toBe(120)
    expect(usageTrace(samples, 'output').maximum).toBe(4)
    const mixed = { ...sample(0, 150, 68, 15), remaining_input: 62 }
    expect(sampleValue(mixed, 'input')).toBe(62)
    expect(sampleValue(sample(0, 20, null, 2), 'input')).toBeNull()
  })

  it('leaves unobserved minutes unknown and preserves an observed zero', () => {
    const samples = [sample(0, 100), sample(60, 100), sample(240, 160)]
    expect(values(samples).slice(-4)).toEqual([0, null, null, 60])
    const unchanged = usageTrace(samples.slice(0, 2), 'input')
    expect(unchanged.state).toBe('bars')
    expect(unchanged.maximum).toBe(0)
    const description = usageTraceDescription(unchanged, 'input')
    expect(description).toContain('0 tokens')
    expect(description).toContain('unavailable')
  })

  it('does not bridge missing counters and invalid dates', () => {
    const missing = [sample(0, 100), sample(60, 130, null), sample(120, 150), sample(180, 160)]
    expect(values(missing).slice(-3)).toEqual([null, null, 10])
    const invalid = [sample(0, 100), { ...sample(60, 120), at: 'unknown' }, sample(120, 150)]
    expect(usageTrace(invalid, 'input').state).toBe('point')
    expect(values(invalid).every(value => value === null)).toBe(true)
    expect(usageTrace([sample(0, 100), sample(60, NaN)], 'input').state).toBe('empty')
  })

  it('shows corrections as gaps instead of clamping negative changes to zero', () => {
    const samples = [sample(0, 100), sample(60, 130), sample(120, 120), sample(180, 125)]
    expect(values(samples).slice(-3)).toEqual([30, null, 5])
    // A correction in the same reporting minute invalidates that bucket, even
    // when a positive increment was observed before or after it.
    expect(values([sample(0, 100), sample(10, 120), sample(20, 110), sample(30, 115)]).at(-1)).toBeNull()
  })

  it('does not accept equal or backwards timestamps as a measured interval', () => {
    const duplicate = [sample(0, 100), sample(0, 150)]
    expect(values(duplicate).every(value => value === null)).toBe(true)
    const backwards = [sample(60, 100), sample(30, 150), sample(120, 160)]
    expect(values(backwards).slice(-3)).toEqual([null, null, 10])
  })

  it('uses the shared clock for active windows and freezes at the final observation otherwise', () => {
    const samples = [sample(0, 100), sample(60, 130)]
    const frozen = usageTrace(samples, 'input')
    expect(frozen.buckets.at(-1)?.value).toBe(30)
    const active = usageTrace(samples, 'input', start + 5 * 60_000)
    expect(active.buckets.at(-1)?.value).toBeNull()
    expect(active.buckets.at(-5)?.value).toBe(30)
    expect(active.endAt).toBe(start + 6 * 60_000)
    expect(usageTrace(samples, 'input', start + 30 * 60_000).state).toBe('empty')
    expect(usageTrace(samples, 'input')).toEqual(frozen)
  })

  it('does not turn retained earlier usage into a first-bucket spike', () => {
    const retained = Array.from({ length: 120 }, (_, index) => sample(index * 5, 10_000 + index * 10))
    const geometry = usageTrace(retained, 'input')
    expect(geometry.buckets.reduce((sum, bucket) => sum + (bucket.value ?? 0), 0)).toBe(1190)
    expect(geometry.buckets.at(-10)?.value).toBe(110)
    expect(geometry.maximum).toBe(120)
  })

  it.each([5, 20, 37])('supports %i one-minute slots without changing the reporting amounts', (barCount) => {
    const samples = [sample(0, 100), sample(60, 130), sample(120, 125), sample(180, 140, null), sample(240, 160)]
    const geometry = usageTrace(samples, 'input', undefined, barCount)
    expect(geometry.buckets).toHaveLength(barCount)
    expect(geometry.endAt! - geometry.startAt!).toBe(barCount * 60_000)
    expect(geometry.buckets.slice(-5).map(bucket => bucket.value)).toEqual([null, 30, null, null, null])
    expect(usageTraceDescription(geometry, 'input')).toContain(`${barCount} one-minute intervals`)
  })

  it.each([[0, 5], [-3, 5], [5.9, 5], [80, 60], [NaN, 20], [Infinity, 20]])('bounds a requested count of %s to %i', (requested, expected) => {
    expect(usageTrace([], 'output', undefined, requested).buckets).toHaveLength(expected)
  })
})
