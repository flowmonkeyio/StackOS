import { describe, expect, it } from 'vitest'
import { ticketStatuses, ticketWorkUrl } from './ticketOverview'

describe('ticket overview navigation', () => {
  it('keeps every canonical status distinct and opens the actual ticket view', () => {
    expect(ticketStatuses).toEqual([
      'not-started',
      'in-progress',
      'complete',
      'deferred',
      'aborted',
      'failed',
      'skipped',
    ])
    expect(ticketWorkUrl(7, 'deferred')).toBe('/projects/7/tasks?view=tickets&status=deferred')
    expect(ticketWorkUrl(7)).toBe('/projects/7/tasks?view=tickets')
  })
})
