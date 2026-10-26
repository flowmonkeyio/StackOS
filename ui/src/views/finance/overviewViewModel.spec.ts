// @vitest-environment node

import { describe, expect, it } from 'vitest'
import { overviewCurrencies, drawingScale, exactChange } from './overviewViewModel'
import { chartProjection, money, metric } from './overviewFixtures'
describe('exact Overview chart projection', () => {
  it('uses complete server totals rather than the paginated rows and leaves source untouched', () => {
    const projection = chartProjection(),
      before = JSON.stringify(projection)
    const [usd] = overviewCurrencies(projection)
    expect(usd!.totals.map((row) => row.currentText)).toEqual(['USD 30.00', 'USD 20.00'])
    expect(usd!.currentWindow).toBe('2026-09-01 through 2026-09-30')
    expect(usd!.trend.map((row) => row.date)).toEqual(['2026-09-01', '2026-09-03'])
    expect(usd!.trendPoints.map((row) => row?.date ?? null)).toEqual([
      '2026-09-01',
      null,
      '2026-09-03',
    ])
    expect(JSON.stringify(projection)).toBe(before)
  })
  it('keeps unknown income and precision unavailable while retaining partial spend', () => {
    const projection = chartProjection()
    projection.metrics_by_currency[0]!.revenue = metric(null, 'unavailable')
    projection.metrics_by_currency[0]!.spend = metric('2000', 'partial')
    const [usd] = overviewCurrencies(projection)
    expect(usd!.totals[0]!.current).toBeNull()
    expect(usd!.totals[0]!.currentText).toBe('Unavailable')
    expect(usd!.totals[1]!.currentState).toBe('partial')
    expect(usd!.trend[0]!.income).toBeNull()
    expect(usd!.trend[0]!.incomeText).toBe('Unavailable')
    projection.metrics_by_currency[0]!.exponent = null
    expect(overviewCurrencies(projection)[0]!.trend[0]!.expenses).toBeNull()
  })
  it('preserves refunds, exact large amounts and separate currencies including prior-only data', () => {
    const projection = chartProjection()
    projection.metrics_by_currency[0]!.revenue = metric('9007199254740993')
    projection.metrics_by_currency[0]!.spend = metric('-1234')
    projection.metrics_by_currency.push({
      currency: 'JPY',
      exponent: 0,
      revenue: metric('42', 'complete', 'JPY', 0),
      spend: metric('1', 'complete', 'JPY', 0),
    })
    projection.comparison = {
      query: { start: '2026-08-02', end: '2026-08-31' },
      metrics_by_currency: [
        {
          currency: 'KWD',
          exponent: 3,
          revenue: metric('1234', 'complete', 'KWD', 3),
          spend: metric('0', 'complete', 'KWD', 3),
        },
      ],
    }
    const currencies = overviewCurrencies(projection)
    expect(currencies.map((row) => row.currency)).toEqual(['JPY', 'KWD', 'USD'])
    const usd = currencies.find((row) => row.currency === 'USD')!
    expect(usd.totals[0]!.currentText).toBe('USD 90,071,992,547,409.93')
    expect(usd.totals[1]!.currentText).toBe('USD −12.34')
    const kwd = currencies.find((row) => row.currency === 'KWD')!
    expect(kwd.totals[0]!.currentText).toBe('Unavailable')
    expect(kwd.totals[0]!.priorText).toBe('KWD 1.234')
    expect(kwd.totals[0]!.changeText).toBe('Unavailable')
  })
  it('computes exact changes across explicit precision without percentages or zero guesses', () => {
    expect(exactChange(money('9007199254740993'), money('9007199254740990'))).toBe('USD 0.03')
    expect(exactChange(money('1234', 'KWD', 3), money('120', 'KWD', 2))).toBe('KWD 0.034')
    expect(exactChange(money('-105'), money('0'))).toBe('USD −1.05')
    expect(exactChange(money('105'), null)).toBe('Unavailable')
    expect(exactChange(money('105'), money('100', 'JPY', 0))).toBe('Unavailable')
    const projection = chartProjection()
    projection.comparison = {
      query: { start: '2026-08-02', end: '2026-08-31' },
      metrics_by_currency: [
        {
          currency: 'USD',
          exponent: 3,
          revenue: metric('0', 'complete', 'USD', 3),
          spend: metric(null, 'unavailable'),
        },
      ],
    }
    const [usd] = overviewCurrencies(projection)
    expect(usd!.priorWindow).toBe('2026-08-02 through 2026-08-31')
    expect(usd!.totals[0]!.changeText).toBe('USD 30.000')
    expect(usd!.totals[1]!.changeText).toBe('Unavailable')
  })
  it('normalizes huge numbers only for geometry and explicitly marks subscale nonzero amounts', () => {
    const huge = money('1' + '0'.repeat(400)),
      tiny = money('1')
    const scale = drawingScale([huge, money('-9007199254740993'), tiny])
    expect(scale.value(huge)).toBe(100000)
    expect(scale.value(money('-1' + '0'.repeat(400)))).toBe(-100000)
    expect(scale.value(tiny)).toBeNull()
    expect(scale.belowScaleCount).toBe(2)
    expect(scale.description).toContain('10^393 USD')
    expect(scale.description).toContain('Exact values')
    expect(drawingScale([money('0')]).value(money('0'))).toBe(0)
    expect(drawingScale([]).value(null)).toBeNull()
  })
  it('does not invent dates or totals for empty/single periods or malformed precision', () => {
    const projection = chartProjection()
    projection.trend = []
    expect(overviewCurrencies(projection)[0]!.trendPoints).toEqual([])
    projection.trend = [
      { date: '0001-01-01', currency: 'USD', revenue_minor: '1', spend_minor: '0' },
    ]
    expect(overviewCurrencies(projection)[0]!.trendPoints).toHaveLength(1)
    projection.metrics_by_currency[0]!.revenue = { state: 'unavailable', amount: money('0') }
    projection.metrics_by_currency[0]!.spend = {
      state: 'complete',
      amount: { minor: '1e9', currency: 'USD', exponent: 2 },
    }
    expect(overviewCurrencies(projection)[0]!.totals.every((row) => row.current === null)).toBe(
      true,
    )
    projection.metrics_by_currency = []
    projection.comparison = null
    expect(overviewCurrencies(projection)).toEqual([])
  })
})
