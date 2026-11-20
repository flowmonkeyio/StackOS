import { describe, expect, it } from 'vitest'
import { DEFAULT_USAGE_BAR_COUNT, prepareUsageSamples, preparedUsageTrace, sampleValue, usageTrace, usageTraceDescription, usageWindowAnchor } from './liveUsage'
import { UsageIntervalOutKind, type SchemaUsageIntervalOut, type SchemaUsageSampleOut } from '@/api'

const start = Date.parse('2026-11-09T00:00:00Z')
const sample = (seconds: number, input: number, cache: number | null = 0, output = 0): SchemaUsageSampleOut => ({
  at: new Date(start + seconds * 1000).toISOString(), input, cache_read: cache, output,
  total: input + output, cache_write: null, reasoning: null, remaining_input: null,
  source: null, intervals: null,
})
const interval = (kind: UsageIntervalOutKind, input: number, start_at: string | null = null): SchemaUsageIntervalOut => ({
  kind, input, remaining_input: input, start_at, output: null, total: null, cache_read: null, cache_write: null, reasoning: null,
})
const values = (samples: SchemaUsageSampleOut[], metric: 'input' | 'cache_read' | 'output' = 'input', endAt?: number) => usageTrace(samples, metric, endAt).buckets.map(bucket => bucket.value)

describe('sparse interval averages', () => {
  const batch = (from: number, to: number, delta: number): SchemaUsageSampleOut => ({
    ...sample(to, 10_000 + delta),
    intervals: [interval(UsageIntervalOutKind.batched, delta, new Date(start + from * 1000).toISOString())],
  })
  it.each([
    [180, [100, 100, 100, null]],
    [144, [125, 125, 50]],
    [218, [300 * 60 / 218, 300 * 60 / 218, 300 * 60 / 218, 300 * 38 / 218]],
  ] as [number, (number | null)[]][])('conserves 300 measured tokens over %i seconds without an endpoint spike', (seconds, expected) => {
    const chart = usageTrace([batch(0, seconds, 300)], 'input')
    expect(chart.state).toBe('bars')
    expected.forEach((value, index) => {
      const actual = chart.buckets.slice(-expected.length)[index]!.value
      if (value == null) expect(actual).toBeNull()
      else expect(actual).toBeCloseTo(value)
    })
    expect(chart.buckets.reduce((sum, bucket) => sum + (bucket.value ?? 0), 0)).toBeCloseTo(300)
    expect(usageTraceDescription(chart, 'input')).toContain('interval average')
  })
  it('clips to window overlap without moving excluded tokens into the first visible bucket', () => {
    // A five-minute window at minute six covers [120, 420); only [120, 360)
    // of this six-minute, 600-token interval belongs to it: 400 tokens.
    const chart = usageTrace([batch(0, 360, 600)], 'input', undefined, 5)
    expect(chart.buckets.map(bucket => bucket.value)).toEqual([100, 100, 100, 100, null])
    expect(chart.buckets.reduce((sum, bucket) => sum + (bucket.value ?? 0), 0)).toBe(400)
    const partial = usageTrace([batch(15, 398, 383)], 'input', undefined, 5)
    expect(partial.buckets.map(bucket => bucket.value)).toEqual([60, 60, 60, 60, 38])
    expect(partial.buckets.reduce((sum, bucket) => sum + (bucket.value ?? 0), 0)).toBe(278)
    expect(usageTrace([batch(0, 360, 600)], 'input', start + 180_000).maximum).toBe(0) // future endpoint
  })
  it('adds overlapping invocation intervals independently, including fractional small amounts and measured zero', () => {
    const chart = usageTrace([batch(0, 180, 1), batch(60, 240, 3)], 'input')
    expect(chart.buckets.slice(-5).map(bucket => bucket.value)).toEqual([1 / 3, 4 / 3, 4 / 3, 1, null])
    expect(chart.buckets.reduce((sum, bucket) => sum + (bucket.value ?? 0), 0)).toBeCloseTo(4)
    expect(usageTrace([batch(0, 180, 0)], 'input').buckets.slice(-4).map(bucket => bucket.value)).toEqual([0, 0, 0, null])
  })
  it('projects only finite nonnegative deltas with known ordered bounds', () => {
    for (const start_at of [null, 'invalid', new Date(start + 180_000).toISOString(), new Date(start + 240_000).toISOString()]) {
      const row = batch(0, 180, 300)
      row.intervals![0]!.start_at = start_at
      expect(usageTrace([row], 'input').maximum).toBe(0)
    }
    for (const delta of [NaN, Infinity, -1]) expect(usageTrace([batch(0, 180, delta)], 'input').maximum).toBe(0)
  })
  it('rejects an entire conflicting endpoint before projecting and recovers with a fresh baseline', () => {
    const row = batch(0, 180, 300)
    const conflict = batch(0, 180, 400)
    expect(usageTrace([row, conflict], 'input').maximum).toBe(0)
    expect(usageTrace([conflict, row], 'input').maximum).toBe(0)
    expect(usageTrace([row, { ...row, intervals: conflict.intervals }], 'input').maximum).toBe(0)
    expect(usageTrace([row, row], 'input')).toEqual(usageTrace([row], 'input'))
    expect(values([row, conflict, sample(240, 500), sample(300, 510)]).slice(-3)).toEqual([null, null, 10])
  })

  it.each(['input', 'cache_read', 'output'] as const)('conserves known %s interval overlap after reset, missing-counter and conflict recovery', metric => {
    const measured = (n: number) => ({ input: n * 2, remaining_input: n, cache_read: n, output: n, total: n * 3 })
    const reading = (seconds: number, n: number, intervals: SchemaUsageIntervalOut[]): SchemaUsageSampleOut => ({
      ...sample(seconds, n * 2, n, n), ...measured(n), intervals,
    })
    const baseline = (seconds: number, n: number) => reading(seconds, n, [{ ...interval(UsageIntervalOutKind.baseline, n), ...measured(n) }])
    const reset = reading(30, 10, [{ ...interval(UsageIntervalOutKind.reset, 0), input: null, remaining_input: null }])
    const missing = { ...reading(10, 10, []), remaining_input: null, cache_read: null }
    const gaps = [[reset], [missing, baseline(30, 10)], [baseline(10, 10), baseline(10, 20), baseline(30, 10)]]
    for (const gap of gaps) {
      const before = [baseline(0, 100), ...gap]
      expect(usageTrace(before, metric).maximum).toBe(0)
      for (const [seconds, expected] of [[210, [50, 100, 100, 50]], [50, [300]]] as const) {
        const batch = reading(seconds, 310, [{ ...interval(UsageIntervalOutKind.batched, 300, new Date(start + 30_000).toISOString()), ...measured(300) }])
        const chart = usageTrace([...before, batch], metric)
        expect(chart.buckets.slice(-expected.length).map(bucket => bucket.value)).toEqual(expected)
        expect(chart.buckets.reduce((sum, bucket) => sum + (bucket.value ?? 0), 0)).toBe(300)
        expect(chart.buckets.slice(0, -expected.length).every(bucket => bucket.value === null)).toBe(true)
        const zero = usageTrace([...before, { ...batch, intervals: [{ ...batch.intervals![0]!, ...measured(0) }] }], metric)
        expect(zero.buckets.slice(-expected.length).map(bucket => bucket.value)).toEqual(expected.map(() => 0))
      }
    }
    // A later uncertain reading in the same minute cannot erase a prior known
    // final-only interval from this or another invocation either.
    const batch = reading(50, 310, [{ ...interval(UsageIntervalOutKind.batched, 300, new Date(start + 30_000).toISOString()), ...measured(300) }])
    expect(usageTrace([baseline(0, 100), reset, batch, { ...reset, at: new Date(start + 55_000).toISOString() }], metric).buckets.at(-1)?.value).toBe(300)
  })
})

describe('reported activity bars', () => {
  it('retains the active minute without dropping current observations or admitting future source samples', () => {
    const prepared = prepareUsageSamples([sample(0, 100), sample(2, 102), sample(40, 140)])
    const early = usageWindowAnchor(prepared, start + 10_000)
    expect(early).toBe(start + 2000)
    expect(usageWindowAnchor(prepared, start + 20_000)).toBe(early)
    expect(preparedUsageTrace(prepared, 'input', early).buckets.at(-1)?.value).toBe(2)
    const later = usageWindowAnchor(prepared, start + 50_000)
    expect(preparedUsageTrace(prepared, 'input', later).buckets.at(-1)?.value).toBe(40)
    expect(usageWindowAnchor(prepared, start + 60_000)).toBe(start + 60_000)
  })
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
    expect(usageTrace(samples, 'input').maximum).toBeCloseTo(30 * 60 / 65)
    for (const [metric, delta] of [['input', 30], ['cache_read', 120], ['output', 4]] as const) {
      expect(usageTrace(samples, metric).buckets.reduce((sum, bucket) => sum + (bucket.value ?? 0), 0)).toBeCloseTo(delta)
    }
    expect(usageTrace(samples, 'cache_read').buckets.at(-1)?.observations[0]?.value).toBe(120)
    expect(usageTrace(samples, 'output').buckets.at(-1)?.observations[0]?.value).toBe(4)
    const mixed = { ...sample(0, 150, 68, 15), remaining_input: 62 }
    expect(sampleValue(mixed, 'input')).toBe(62)
    expect(sampleValue(sample(0, 20, null, 2), 'input')).toBeNull()
  })

  it('averages known sparse changes and preserves an observed zero', () => {
    const samples = [sample(0, 100), sample(60, 100), sample(240, 160)]
    expect(values(samples).slice(-4)).toEqual([20, 20, 20, null])
    expect(usageTrace(samples, 'input').buckets.at(-1)?.observations[0]).toMatchObject({ kind: 'batched', value: 60, startAt: start + 60_000 })
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
    // A malformed later counter cannot erase the earlier observed baseline.
    expect(usageTrace([sample(0, 100), sample(60, NaN)], 'input').state).toBe('point')
  })

  it('shows corrections as gaps instead of clamping negative changes to zero', () => {
    const samples = [sample(0, 100), sample(60, 130), sample(120, 120), sample(180, 125)]
    expect(values(samples).slice(-3)).toEqual([30, null, 5])
    // A correction in the same reporting minute invalidates that bucket, even
    // when a positive increment was observed before or after it.
    expect(values([sample(0, 100), sample(10, 120), sample(20, 110), sample(30, 115)]).at(-1)).toBeNull()
  })

  it('does not accept conflicting equal timestamps and orders source history before differencing', () => {
    const duplicate = [sample(0, 100), sample(0, 150)]
    expect(values(duplicate).every(value => value === null)).toBe(true)
    const backwards = [sample(60, 100), sample(30, 150), sample(120, 160)]
    expect(values(backwards).slice(-3)).toEqual([null, null, 60])
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

  it('uses per-invocation evidence so a new retained baseline cannot inflate an existing trace', () => {
    const points: SchemaUsageSampleOut[] = [
      { ...sample(0, 100), intervals: [interval(UsageIntervalOutKind.baseline, 100)] },
      { ...sample(15, 1100), intervals: [interval(UsageIntervalOutKind.baseline, 1000)] },
      { ...sample(30, 5110), intervals: [interval(UsageIntervalOutKind.checkpoint, 4010, new Date(start + 15000).toISOString())] },
      { ...sample(240, 9110), intervals: [interval(UsageIntervalOutKind.batched, 4000, new Date(start + 30000).toISOString())] },
    ]
    const chart = usageTrace(points, 'input')
    expect(chart.maximum).toBeCloseTo(4010 + 4000 * 30 / 210) // checkpoint plus the overlapping interval average
    expect(chart.buckets.reduce((sum, bucket) => sum + (bucket.value ?? 0), 0)).toBeCloseTo(8010) // neither baseline contributes
    expect(chart.buckets.at(-1)?.value).toBeNull()
    expect(chart.buckets.at(-1)?.observations[0]?.value).toBe(4000)
    expect(usageTraceDescription(chart, 'input')).toContain('batched observation')
    expect(usageTraceDescription(chart, 'input')).toContain('consumption time unavailable')
    expect(usageTrace([...points].reverse().concat(points[0]!), 'input')).toEqual(chart)
  })

  it('keeps a reset interval visibly and accessibly uncertain within its minute', () => {
    const reset = { ...interval(UsageIntervalOutKind.reset, 0), input: null, remaining_input: null }
    const chart = usageTrace([
      { ...sample(0, 100), intervals: [interval(UsageIntervalOutKind.baseline, 100)] },
      { ...sample(10, 200), intervals: [interval(UsageIntervalOutKind.checkpoint, 100)] },
      { ...sample(20, 10), intervals: [reset] },
    ], 'input')
    expect(chart.maximum).toBe(0)
    expect(chart.buckets.at(-1)?.value).toBeNull()
    expect(usageTraceDescription(chart, 'input')).toContain('counter reset')
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
