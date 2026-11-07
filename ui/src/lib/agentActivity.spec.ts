import { describe, expect, it } from 'vitest'
import { agentStatsFixture } from '@/views/home/agentStatsFixtures'
import { activitySeries, cacheShare, exactUsage, fieldCoverage, formatTokens, localActivityFilters, usageCoverage, usageLabel, usageValue } from './agentActivity'

describe('Shared recorded token formatting', () => {
  it('formats exact and compact values and discloses field-level missing counts independently', () => {
    const metrics = agentStatsFixture().totals
    expect(formatTokens(513050000)).toBe('513.05M')
    expect(formatTokens(1234567, false)).toBe('1,234,567')
    expect(formatTokens(0)).toBe('0')
    expect(formatTokens(null)).toBe('Not reported')
    metrics.coverage.fields!.cache_read!.unavailable_attempts = 1
    expect(fieldCoverage(metrics, 'cache_read')).toBe('Cache missing for 1 attempt')
    expect(fieldCoverage(metrics, 'input')).toBe('')
    expect(cacheShare(metrics)).toBeNull()
    expect(exactUsage(metrics, 'input')).toContain('70 tokens')
  })

  it('renders backend input decomposition and independent color projections without inventing unknown cache', () => {
    const days = agentStatsFixture().daily
    days[0]!.reported_usage = { ...days[0]!.reported_usage, input: 100, cache_read: 40, remaining_input: 30, input_with_unknown_cache: 30 }
    expect(activitySeries(days, 'input', 'input_type').map(item => item.values)).toEqual([[40], [30], [30]])
    days[0]!.by_model[0]!.model = null
    expect(activitySeries(days, 'attempts', 'model')[0]).toMatchObject({ label: 'Model not recorded', values: [4] })
    expect(activitySeries(days, 'input', 'agent', true)[0]!.label).toBe('same-name · Fixture project')
    const filters = localActivityFilters(new Date(2026, 0, 2))
    expect(filters).toMatchObject({ start_date: '2025-12-27', end_date: '2026-01-02', group_by: 'model' })
    expect(filters.timezone).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone)
  })
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
