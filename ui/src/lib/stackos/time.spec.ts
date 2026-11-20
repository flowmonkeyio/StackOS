// @vitest-environment node

import { describe, expect, it } from 'vitest'
import { WorkDurationOutCoverage } from '@/api'

import {
  formatDurationBetween,
  formatDurationMinutes,
  formatWorkTime,
  formatRelativeDateTime,
  formatShortDate,
  newestFirst,
} from './time'

const NOW = new Date('2026-06-09T12:00:00Z')

const live = { invocation_id: 'owned', agent: 'delivery', host_key: 'codex', model: 'model', ticket_key: 'child', started_at: '2026-06-09T11:00:00Z' }
const duration = { coverage: WorkDurationOutCoverage.complete, total_seconds: 1200, observed_at: '2026-06-09T11:59:00Z', endpoint_at: '2026-06-09T11:59:00Z', recorded_intervals: 2, unavailable_intervals: 0, live: [live], issues: [] }

describe('formatWorkTime', () => {
  it('formats the measured union and extends it once for an exact owned live identity', () => {
    expect(formatWorkTime(duration, live, NOW)).toBe('21m')
    expect(formatWorkTime({ ...duration, live: [live, { ...live, invocation_id: 'parallel' }] }, live, NOW)).toBe('21m')
    expect(formatWorkTime({ ...duration, total_seconds: 0, live: [] }, null, NOW)).toBe('0m')
    expect(formatWorkTime({ ...duration, total_seconds: 20, live: [] }, null, NOW)).toBe('<1m')
  })

  it('freezes at the recorded observation after stop, final receipt, remount or an unrelated execution', () => {
    expect(formatWorkTime(duration, null, NOW)).toBe('20m')
    expect(formatWorkTime({ ...duration, live: [] }, live, new Date('2026-06-10T12:00:00Z'))).toBe('20m')
    for (const key of ['invocation_id', 'agent', 'host_key', 'model', 'ticket_key', 'started_at'] as const) {
      expect(formatWorkTime(duration, { ...live, [key]: 'other' }, NOW)).toBe('20m')
    }
    expect(formatWorkTime({ ...duration, live: [{ ...live, invocation_id: null }] }, { ...live, invocation_id: null }, NOW)).toBe('20m')
  })

  it('keeps partial totals, unavailable history and clock anomalies honest', () => {
    expect(formatWorkTime({ ...duration, coverage: WorkDurationOutCoverage.partial }, null, NOW)).toBe('20m')
    expect(formatWorkTime(null, live, NOW)).toBe('N/A')
    expect(formatWorkTime({ ...duration, coverage: WorkDurationOutCoverage.unavailable }, live, NOW)).toBe('N/A')
    for (const total_seconds of [null, -1, NaN, Infinity]) {
      expect(formatWorkTime({ ...duration, total_seconds }, live, NOW)).toBe('N/A')
    }
    for (const observed_at of [null, 'invalid', '2026-06-09T13:00:00Z']) {
      expect(formatWorkTime({ ...duration, observed_at }, live, NOW)).toBe('20m')
    }
  })
})

describe('formatShortDate', () => {
  it('keeps older years distinguishable and normalizes daemon UTC timestamps', () => {
    const current = formatShortDate('2026-06-09T12:00:00', NOW)
    expect(current).toBe(formatShortDate('2026-06-09T12:00:00Z', NOW))
    expect(current).not.toContain('2026')
    expect(formatShortDate('2025-06-09T12:00:00Z', NOW)).toContain('2025')
  })

  it('handles absent and malformed creation dates', () => {
    expect(formatShortDate(null, NOW)).toBe('-')
    expect(formatShortDate('not-a-date', NOW)).toBe('not-a-date')
  })
})

describe('formatRelativeDateTime', () => {
  it('handles empty and invalid values', () => {
    expect(formatRelativeDateTime(null, NOW)).toBe('-')
    expect(formatRelativeDateTime(undefined, NOW)).toBe('-')
    expect(formatRelativeDateTime('not-a-date', NOW)).toBe('not-a-date')
  })

  it('formats past timestamps across scales', () => {
    expect(formatRelativeDateTime('2026-06-09T11:59:40Z', NOW)).toBe('just now')
    expect(formatRelativeDateTime('2026-06-09T11:14:00Z', NOW)).toBe('46m ago')
    expect(formatRelativeDateTime('2026-06-09T03:00:00Z', NOW)).toBe('9h ago')
    expect(formatRelativeDateTime('2026-06-03T12:00:00Z', NOW)).toBe('6d ago')
  })

  it('falls back to a date for older timestamps', () => {
    const out = formatRelativeDateTime('2026-01-09T12:00:00Z', NOW)
    expect(out).toMatch(/2026/)
  })

  it('formats future timestamps', () => {
    expect(formatRelativeDateTime('2026-06-09T12:30:00Z', NOW)).toBe('in 30m')
    expect(formatRelativeDateTime('2026-06-10T13:00:00Z', NOW)).toBe('in 1d')
  })

  it('treats daemon ISO timestamps without a zone as UTC', () => {
    expect(formatRelativeDateTime('2026-06-09T11:00:00', NOW)).toBe('1h ago')
  })
})

describe('formatDurationMinutes', () => {
  it('handles edge values', () => {
    expect(formatDurationMinutes(null)).toBe('-')
    expect(formatDurationMinutes(undefined)).toBe('-')
    expect(formatDurationMinutes(0.4)).toBe('<1m')
  })

  it('formats across scales', () => {
    expect(formatDurationMinutes(14)).toBe('14m')
    expect(formatDurationMinutes(142)).toBe('2h 22m')
    expect(formatDurationMinutes(937)).toBe('15h 37m')
    expect(formatDurationMinutes(1500)).toBe('1d 1h')
    expect(formatDurationMinutes(1440)).toBe('1d')
  })
})

describe('formatDurationBetween', () => {
  it('computes closed and open-ended ranges', () => {
    expect(
      formatDurationBetween('2026-06-09T10:00:00Z', '2026-06-09T11:30:00Z', NOW),
    ).toBe('1h 30m')
    expect(formatDurationBetween('2026-06-09T11:00:00Z', null, NOW)).toBe('1h')
    expect(formatDurationBetween(null, null, NOW)).toBe('-')
  })
})

describe('newestFirst', () => {
  it('sorts descending by the picked timestamp without mutating input', () => {
    const items = [
      { id: 1, created_at: '2026-06-01T00:00:00Z' },
      { id: 3, created_at: '2026-06-09T00:00:00Z' },
      { id: 2, created_at: '2026-06-05T00:00:00Z' },
    ]
    const sorted = newestFirst(items, (item) => item.created_at)
    expect(sorted.map((item) => item.id)).toEqual([3, 2, 1])
    expect(items.map((item) => item.id)).toEqual([1, 3, 2])
  })

  it('sinks rows without timestamps to the bottom', () => {
    const items = [
      { id: 1, at: null },
      { id: 2, at: '2026-06-05T00:00:00Z' },
    ]
    expect(newestFirst(items, (item) => item.at).map((item) => item.id)).toEqual([2, 1])
  })
})
