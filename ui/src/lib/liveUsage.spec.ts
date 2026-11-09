import { describe, expect, it } from 'vitest'
import { sampleValue, stepTrace } from './liveUsage'
import type { SchemaUsageSampleOut } from '@/api'

const sample = (at: string, input: number, cache: number | null, output: number): SchemaUsageSampleOut => ({ at, input, cache_read: cache, output, total: input + output, cache_write: null, reasoning: null, remaining_input: null })
describe('measured current-call traces', () => {
  it('uses paired native counters and independent scales with discrete steps and no zero prefix', () => {
    const samples = [sample('2026-11-08T01:00:00Z', 100, 80, 5), sample('2026-11-08T01:00:05Z', 120, 90, 9)]
    expect(sampleValue(samples[0]!, 'input')).toBe(20)
    for (const metric of ['input', 'cache_read', 'output'] as const) expect(stepTrace(samples, metric)).toBe('M1.00,11.00 H27.00V1.00')
    expect(stepTrace(samples.slice(0, 1), 'input')).toBe('')
    expect(stepTrace([...samples, sample('2026-11-08T01:00:10Z', 200, null, 12)], 'input')).toBe('')
    expect(stepTrace(samples, 'output')).not.toMatch(/[CQ]/)
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
})
