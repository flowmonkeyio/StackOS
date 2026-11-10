import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import FinanceOverview from './FinanceOverview.vue'
import { chartProjection } from './overviewFixtures'
import { financeRecordLabel, reportingReadiness } from './reportingReadiness'

describe('legacy reporting readiness', () => {
  it('uses full-population readiness instead of paginated rows', () => {
    const projection = chartProjection()
    projection.reporting_readiness = {
      missing_metadata_count: 31,
      missing_precision_count: 62,
      selected_record_count: 65,
    }
    projection.coverage = {
      state: 'unavailable',
      issues: ['missing_or_conflicting_reporting_policy'],
    }
    expect(reportingReadiness(projection).missing).toBe(31)
    expect(reportingReadiness(projection).precision).toBe(62)
    expect(reportingReadiness(projection).total).toBe(65)
  })
  it('does not render empty charts or unavailable metric cards when no totals are supported', () => {
    const projection = chartProjection()
    projection.metrics_by_currency = [
      {
        currency: 'USD',
        spend: { state: 'unavailable', amount: null },
        revenue: { state: 'unavailable', amount: null },
        profit: { state: 'unavailable', amount: null },
      },
    ]
    const wrapper = mount(FinanceOverview, {
      props: { projection },
      global: { stubs: { FinanceOverviewCharts: true } },
    })
    expect(wrapper.text()).toContain('No supported accounting totals yet')
    expect(wrapper.findComponent({ name: 'FinanceOverviewCharts' }).exists()).toBe(false)
    expect(wrapper.findAllComponents({ name: 'UiMetricCard' })).toHaveLength(0)
  })
  it('labels undated records beyond the current page without leaking internal IDs', () => {
    const projection = chartProjection()
    projection.record_labels = { 'receipt:mongo': 'MongoDB Atlas · Date not recorded' }
    expect(financeRecordLabel(projection, 'receipt:mongo')).toBe(
      'MongoDB Atlas · Date not recorded',
    )
    expect(financeRecordLabel(projection, 'receipt:unknown')).toBe('Inspect record details')
  })
  it('retains compatibility with older projections and never manufactures metrics', () => {
    const projection = chartProjection()
    projection.exclusions = [{ record_ref: 'book:one', reason: 'missing_metadata' }]
    expect(reportingReadiness(projection).missing).toBe(1)
    expect(reportingReadiness(projection).hasMetrics).toBe(true)
  })
  it('preserves supported prior-period charts when this period has no totals', () => {
    const projection = chartProjection()
    projection.comparison = {
      metrics_by_currency: projection.metrics_by_currency,
      query: projection.query,
    }
    projection.metrics_by_currency = []
    const wrapper = mount(FinanceOverview, {
      props: { projection },
      global: { stubs: { FinanceOverviewCharts: true } },
    })
    expect(reportingReadiness(projection).hasMetrics).toBe(false)
    expect(wrapper.findComponent({ name: 'FinanceOverviewCharts' }).exists()).toBe(true)
  })
  it('explains stale policy and precision-only review without missing-metadata entries', () => {
    const projection = chartProjection()
    projection.reporting_readiness = {
      state: 'needs_review',
      missing_precision_count: 2,
      reasons: [{ reason: 'stale_metadata', count: 1 }],
    }
    projection.coverage = { state: 'partial', issues: ['policy_conflict'] }
    expect(reportingReadiness(projection).reasons).toContain('stale metadata')
    expect(reportingReadiness(projection).coverageIssues).toContain('policy conflict')
    expect(reportingReadiness(projection).precision).toBe(2)
  })
})
