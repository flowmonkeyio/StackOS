import { describe, expect, it } from 'vitest'
import { agentStatsFixture } from '@/views/home/agentStatsFixtures'
import { activitySeries, cacheShare, exactUsage, fieldCoverage, formatTokens, localActivityFilters } from './agentActivity'

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
  it('preserves measured zero, independent missing fields, empty and absent projection states', () => {
    const metrics = agentStatsFixture().totals
    metrics.reported_usage.input = 0
    expect(exactUsage(metrics, 'input')).toContain('Input: 0 tokens')
    metrics.reported_usage.cache_read = null
    expect(fieldCoverage(metrics, 'cache_read')).toBe('Cache not reported')
    expect(exactUsage(metrics, 'output')).toContain('30 tokens')
    metrics.launch_attempts = 0
    expect(fieldCoverage(metrics, 'input')).toBe('No recorded usage')
    for (const absent of [null, undefined]) {
      expect(fieldCoverage(absent, 'input')).toBe('Input not reported')
    }
  })
})
