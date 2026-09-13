import type { PortfolioProject, TicketCountSummary, TicketCounts } from './ticketOverview'

export function ticketCountsFixture(overrides: Partial<TicketCounts> = {}): TicketCounts {
  return {
    'not-started': 0,
    'in-progress': 1,
    complete: 2,
    deferred: 0,
    aborted: 0,
    failed: 1,
    skipped: 0,
    ...overrides,
  }
}

export function ticketSummaryFixture(
  overrides: Partial<TicketCountSummary> = {},
): TicketCountSummary {
  return {
    project_id: null,
    is_active: true,
    as_of: '2026-09-07T23:00:00Z',
    total_count: 4,
    ticket_counts: ticketCountsFixture(),
    ...overrides,
  }
}

export function portfolioFixture(overrides: Partial<PortfolioProject> = {}): PortfolioProject {
  return {
    id: 1,
    name: 'Business One',
    slug: 'business-one',
    domain: 'business.test',
    is_active: true,
    created_at: '2026-09-01T07:00:00Z',
    updated_at: '2026-09-07T20:00:00Z',
    last_activity_at: '2026-09-07T20:00:00Z',
    latest_task: {
      id: 2,
      key: 'delivered',
      title: 'Delivered report',
      status: 'complete',
      updated_at: '2026-09-07T20:00:00Z',
    },
    ticket_count: 4,
    ticket_counts: ticketCountsFixture(),
    ...overrides,
  }
}
