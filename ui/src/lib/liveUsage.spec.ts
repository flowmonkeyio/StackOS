import { describe, expect, it } from 'vitest'
import { sampleValue, usageTrace } from './liveUsage'
import type { SchemaUsageSampleOut } from '@/api'

const sample = (at: string, input: number, cache: number | null, output: number): SchemaUsageSampleOut => ({ at, input, cache_read: cache, output, total: input + output, cache_write: null, reasoning: null, remaining_input: null })
describe('measured trace geometry', () => {
  it('uses paired native counters and independent scales with discrete steps and no zero prefix', () => {
    const samples = [sample('2026-11-08T01:00:00Z', 100, 80, 5), sample('2026-11-08T01:00:05Z', 120, 90, 9)]
    expect(sampleValue(samples[0]!, 'input')).toBe(20)
    for (const metric of ['input', 'cache_read', 'output'] as const) expect(usageTrace(samples, metric)).toEqual({ state: 'trace', points: [{ x: 1, y: 11 }, { x: 27, y: 1 }] })
    expect(usageTrace(samples.slice(0, 1), 'input')).toEqual({ state: 'point', points: [{ x: 14, y: 6 }] })
    expect(usageTrace([...samples, sample('2026-11-08T01:00:10Z', 200, null, 12)], 'input')).toEqual({ state: 'empty', points: [] })
    expect(usageTrace([], 'output')).toEqual({ state: 'empty', points: [] })
  })
  it('uses the lifetime paired subtotal when another invocation has unknown cache', () => {
    const mixed = { ...sample('2026-11-08T01:00:00Z', 150, 68, 15), remaining_input: 62 }
    expect(sampleValue(mixed, 'input')).toBe(62)
    expect(sampleValue(mixed, 'cache_read')).toBe(68)
    expect(sampleValue(mixed, 'output')).toBe(15)
    const unknown = sample('2026-11-08T01:00:05Z', 20, null, 2)
    expect(sampleValue(unknown, 'input')).toBeNull()
    expect(sampleValue(unknown, 'output')).toBe(2)
  })
  it('uses measured time spacing, flat zero lines and a dot for a single timestamp without inventing history', () => {
    const samples = [0, 1, 5].map(second => sample(`2026-11-08T01:00:0${second}Z`, 0, 0, 0))
    expect(usageTrace(samples, 'input')).toEqual({ state: 'trace', points: [{ x: 1, y: 6 }, { x: 6.2, y: 6 }, { x: 27, y: 6 }] })
    expect(usageTrace([samples[0]!, samples[0]!], 'input').state).toBe('point')
    expect(usageTrace([samples[0]!, { ...samples[1]!, at: 'unknown' }], 'output').state).toBe('empty')
  })
})
