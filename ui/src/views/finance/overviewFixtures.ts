import type { SchemaFinanceProjectionOut } from '@/api'

export const money = (minor: string, currency = 'USD', exponent = 2) => ({
  minor,
  currency,
  exponent,
})
export const metric = (
  minor: string | null,
  state = 'complete',
  currency = 'USD',
  exponent = 2,
) => ({
  state,
  amount: minor === null ? null : money(minor, currency, exponent),
  included_record_refs: ['book:one'],
})
export function chartProjection(): SchemaFinanceProjectionOut {
  return {
    snapshot: {
      revision: 1,
      sha256: 'a'.repeat(64),
      read_at: '2026-09-30',
      schema_version: 'local-json-v1',
      custody_issues: [],
    },
    query: {
      start: '2026-09-01',
      end: '2026-09-30',
      recognition_basis: 'cash',
      date_basis: 'transaction',
      timezone: 'UTC',
    },
    rows: [],
    page: { total: 100, offset: 0, limit: 1, has_more: true },
    metrics_by_currency: [
      {
        currency: 'USD',
        exponent: 2,
        revenue: metric('3000'),
        spend: metric('2000'),
        profit: metric('1000'),
      },
    ],
    breakdowns: {},
    trend: [
      {
        date: '2026-09-01',
        currency: 'USD',
        revenue_minor: '3000',
        spend_minor: '0',
        included_record_refs: ['book:income'],
      },
      {
        date: '2026-09-03',
        currency: 'USD',
        revenue_minor: '0',
        spend_minor: '2000',
        included_record_refs: ['book:expense'],
      },
    ],
    coverage: { state: 'complete', issues: [] },
    exclusions: [],
    review_items: [],
    undated_or_period_only: [],
    warnings: [],
    comparison: null,
  }
}
