import { describe, expect, it } from 'vitest'
import { agentStatsFixture, groupedAgentStatsFixture } from '@/views/home/agentStatsFixtures'
import { activitySeries, agentLabel, cacheShare, exactUsage, fieldCoverage, formatTokens, formatUsage, localActivityFilters, reviewFacts, usageFields, usageValue } from './agentActivity'

describe('Shared recorded token formatting', () => {
  it.each(['agent', 'model', 'agent_model'] as const)('projects canonical %s groups with exact labels and stable colors', group => {
    const fixture = groupedAgentStatsFixture(group)
    const series = activitySeries(fixture.daily, 'agents', false, group)
    const expected = group === 'agent' ? { 'Agent A': 300, 'Agent B': 300 }
      : group === 'model' ? { 'gpt-6.1-sol': 400, 'gpt-6-sol': 200 }
        : { 'Agent A · gpt-6.1-sol': 100, 'Agent A · gpt-6-sol': 200, 'Agent B · gpt-6.1-sol': 300 }
    expect(Object.fromEntries(series.map(item => [item.label, item.values[0]]))).toEqual(expected)
    expect(Object.fromEntries(series.map(item => [item.key, item.values[0]]))).toEqual(Object.fromEntries(fixture.rows.map(row => [row.group_key, row.reported_usage.total])))
    const reversed = structuredClone(fixture.daily)
    for (const day of reversed) { day.by_agent.reverse(); day.by_model.reverse(); day.by_agent_model.reverse() }
    expect(activitySeries(reversed, 'agents', false, group).sort((a, b) => a.key.localeCompare(b.key))).toEqual([...series].sort((a, b) => a.key.localeCompare(b.key)))
    const sparse = structuredClone(fixture.daily)
    for (const day of sparse) { day.by_agent.splice(0, 1); day.by_model.splice(0, 1); day.by_agent_model.splice(0, 1) }
    for (const item of activitySeries(sparse, 'agents', false, group)) expect(item.token).toBe(series.find(original => original.key === item.key)!.token)
  })

  it('requires measurement evidence for empty, planning and running placeholders, including numeric zeros', () => {
    const metrics = agentStatsFixture().undated
    for (const launches of [0, 1]) {
      metrics.launch_attempts = launches
      expect(usageFields.map(field => usageValue(metrics, field.key))).toEqual([null, null, null])
      expect(exactUsage(metrics, 'input')).toBe('Input: N/A')
      expect(formatUsage(usageValue(metrics, 'output'))).toBe('N/A')
    }
    metrics.coverage = structuredClone(agentStatsFixture().totals.coverage)
    expect(usageFields.map(field => usageValue(metrics, field.key))).toEqual([0, 0, 0])
    metrics.reported_usage.output = 12
    expect(usageValue(metrics, 'output')).toBe(12)
    metrics.coverage.fields!.cache_read!.measured_attempts = 0
    expect(usageValue(metrics, 'input')).toBeNull()
    expect(usageValue(metrics, 'cache_read')).toBeNull()
    expect(usageValue(metrics, 'output')).toBe(12)
    metrics.reported_usage.output = null
    expect(usageValue(metrics, 'output')).toBeNull()
    expect(formatTokens(0)).toBe('0')
  })

  it('keeps unmeasured and absent chart samples null in both views', () => {
    const measured = agentStatsFixture().daily[0]!
    const empty = { ...agentStatsFixture().undated, date: '2026-11-07', by_agent: [], by_model: [], by_agent_model: [] }
    const unavailable = { ...structuredClone(empty), date: '2026-11-08', launch_attempts: 1, by_agent: [{ ...structuredClone(measured.by_agent[0]!), ...agentStatsFixture().undated, launch_attempts: 1 }] }
    const days = [measured, empty, unavailable]
    expect(activitySeries(days, 'tokens').map(item => item.values)).toEqual([[30, null, null], [40, null, null], [30, null, null]])
    expect(activitySeries(days, 'agents')[0]!.values).toEqual([100, null, null])
    expect(activitySeries([empty, unavailable], 'tokens')).toEqual([])
    expect(activitySeries([empty, unavailable], 'agents')).toEqual([])
  })

  it('maps disjoint Input, Cache, Output without changing inclusive counters or totals', () => {
    const metrics = agentStatsFixture().totals
    metrics.reported_usage = { ...metrics.reported_usage, input: 11545485, remaining_input: 312717, cache_read: 11232768, output: 46397, total: 11591882 }
    const before = structuredClone(metrics)
    expect(usageFields.map(field => field.label)).toEqual(['Input', 'Cache', 'Output'])
    expect(usageFields.map(field => usageValue(metrics, field.key))).toEqual([312717, 11232768, 46397])
    expect(usageFields.reduce((sum, field) => sum + usageValue(metrics, field.key)!, 0)).toBe(11591882)
    expect(exactUsage(metrics, 'input')).toBe('Input: 312,717 tokens.')
    expect(metrics).toEqual(before)
    metrics.reported_usage = { ...metrics.reported_usage, input: 11550000, cache_read: 11230000, remaining_input: 320000 }
    expect(formatTokens(usageValue(metrics, 'input'))).toBe('320K')
  })

  it('uses paired coverage for Input and never invents an unknown-cache remainder', () => {
    const metrics = agentStatsFixture().totals
    metrics.reported_usage = { ...metrics.reported_usage, input: 150, cache_read: 80, remaining_input: 20, output: 8, total: 158, input_with_unknown_cache: 50 }
    metrics.coverage.fields!.cache_read!.unavailable_attempts = 1
    expect(usageValue(metrics, 'input')).toBe(20)
    expect(fieldCoverage(metrics, 'input')).toBe('Input missing for 1 attempt')
    metrics.reported_usage.remaining_input = null
    expect(usageValue(metrics, 'input')).toBeNull()
    expect(exactUsage(metrics, 'input')).toBe('Input: N/A')
    metrics.reported_usage.remaining_input = 0
    expect(usageValue(metrics, 'input')).toBe(0)
  })
  it('formats exact and compact values and discloses field-level missing counts independently', () => {
    const metrics = agentStatsFixture().totals
    expect(formatTokens(513050000)).toBe('513.05M')
    expect(formatTokens(1234567, false)).toBe('1,234,567')
    expect(formatTokens(0)).toBe('0')
    expect(formatTokens(null)).toBe('Not reported')
    metrics.coverage.fields!.cache_read!.unavailable_attempts = 1
    expect(fieldCoverage(metrics, 'cache_read')).toBe('Cache missing for 1 attempt')
    expect(fieldCoverage(metrics, 'input')).toBe('Input missing for 1 attempt')
    expect(cacheShare(metrics)).toBeNull()
    expect(exactUsage(metrics, 'input')).toContain('30 tokens')
  })

  it('uses paired measured cache composition and the same Input + Output total by agent', () => {
    const days = agentStatsFixture().daily
    expect(activitySeries(days, 'tokens').map(item => item.values)).toEqual([[30], [40], [30]])
    expect(activitySeries(days, 'agents')[0]!.values).toEqual([100])
    days[0]!.reported_usage = { ...days[0]!.reported_usage, input: 100, cache_read: 40, remaining_input: 30, input_with_unknown_cache: 30 }
    // Unknown cache on another receipt must not inflate Input to 60.
    expect(activitySeries(days, 'tokens').map(item => item.values)).toEqual([[30], [40], [30]])
    expect(activitySeries(days, 'agents', true)[0]!.label).toBe('same-name · Fixture project')
    days[0]!.reported_usage.cache_read = days[0]!.reported_usage.remaining_input = null
    expect(activitySeries(days, 'tokens').map(item => item.key)).toEqual(['output'])
    days[0]!.reported_usage.output = 0
    expect(activitySeries(days, 'tokens')[0]!.values).toEqual([0])
    const unknown = structuredClone(days[0]!)
    unknown.reported_usage.total = null
    unknown.by_agent = []
    days.push(unknown)
    expect(activitySeries(days, 'agents')[0]!.values).toEqual([100, null])
    const filters = localActivityFilters(new Date(2026, 0, 2))
    expect(filters).toMatchObject({ start_date: '2025-12-27', end_date: '2026-01-02', group_by: 'model' })
    expect(filters.timezone).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone)
  })
  it('keeps plain review counts and readable agent names without erasing exact versions', () => {
    const metrics = agentStatsFixture().totals
    metrics.review_outcomes = { success: 2, repair: 1, failed: 1, blocked: 1, validation_failed: 1 }
    expect(reviewFacts(metrics)).toBe('Reviews passed: 2 · Changes requested: 1 · Reviews failed: 1 · Reviews blocked: 1 · Invalid review results: 1')
    metrics.review_outcomes = {}
    expect(reviewFacts(metrics)).toBe('')
    expect(agentLabel('sdlc_delivery_reviewer')).toBe('sdlc_delivery_reviewer')
    expect(agentLabel('Customer_Provided NAME')).toBe('Customer_Provided NAME')
  })
  it('preserves measured zero, independent missing fields, empty and absent projection states', () => {
    const metrics = agentStatsFixture().totals
    metrics.reported_usage.input = 0
    metrics.reported_usage.remaining_input = 0
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
