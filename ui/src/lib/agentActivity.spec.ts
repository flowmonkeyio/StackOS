import { describe, expect, it } from 'vitest'
import { agentStatsFixture } from '@/views/home/agentStatsFixtures'
import { usageCoverage, usageLabel, usageValue } from './agentActivity'

describe('Shared recorded token formatting', () => {
  it('preserves measured zero, partial, unavailable, empty and absent projection states', () => {
    const metrics = agentStatsFixture().totals
    metrics.reported_usage.total = 0
    expect(usageValue(metrics)).toBe('0')
    expect(usageLabel(metrics)).toBe('0 tokens')
    metrics.reported_usage.total = 1234567
    metrics.coverage.usage_status = 'partial' as never
    metrics.coverage.usage_measured_attempts = 1
    expect(usageLabel(metrics)).toBe('1,234,567 tokens · Partial')
    expect(usageCoverage(metrics)).toBe('Partial · 1 of 4 attempts measured')
    metrics.reported_usage.total = null
    metrics.coverage.usage_status = 'unavailable' as never
    expect(usageLabel(metrics)).toBe('Tokens unavailable')
    metrics.coverage.usage_status = 'empty' as never
    metrics.launch_attempts = 0
    expect(usageLabel(metrics)).toBe('No recorded attempts')
    expect(usageCoverage(metrics)).toBe('No recorded attempts')
    for (const absent of [null, undefined]) {
      expect(usageLabel(absent)).toBe('Tokens unavailable')
      expect(usageCoverage(absent)).toBe('Usage not available')
    }
  })
})
