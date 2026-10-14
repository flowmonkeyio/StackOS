import { describe, expect, it } from 'vitest'
import { decimalToMinor, formatMoney, minorToDecimal, timeframe, comparisonWindow } from './finance'

describe('exact finance presentation', () => {
  it('formats exact large, negative and mixed precision amounts without rounding', () => {
    expect(formatMoney({ minor: '9007199254740993', currency: 'USD', exponent: 2 })).toBe(
      'USD 90,071,992,547,409.93',
    )
    expect(formatMoney({ minor: '-105', currency: 'JPY', exponent: 0 })).toBe('JPY −105')
    expect(formatMoney({ minor: '1234', currency: 'KWD', exponent: 3 })).toBe('KWD 1.234')
    expect(formatMoney(null)).toBe('Unavailable')
    expect(formatMoney({ minor: '105', currency: 'USD', exponent: null })).toContain(
      'precision unavailable',
    )
  })
  it('converts human amounts as strings and rejects guessed precision', () => {
    expect(decimalToMinor('90071992547409.93', 2)).toBe('9007199254740993')
    expect(minorToDecimal('-1', 3)).toBe('-0.001')
    expect(() => decimalToMinor('1.234', 2)).toThrow()
    expect(() => decimalToMinor('1e3', 2)).toThrow()
    expect(() => decimalToMinor('1.00', null)).toThrow('precision')
  })
  it('keeps historical windows explicit and clamps leap-day comparisons', () => {
    expect(timeframe('all', new Date('2026-10-13T12:00:00Z'))).toEqual({
      start: '0001-01-01',
      end: '9999-12-31',
    })
    expect(comparisonWindow('2024-02-29', '2024-02-29', 'year')).toEqual({
      start: '2023-02-28',
      end: '2023-02-28',
      clamped: true,
    })
    expect(comparisonWindow('2026-09-01', '2026-09-30', 'period')).toEqual({
      start: '2026-08-02',
      end: '2026-08-31',
      clamped: null,
    })
    expect(comparisonWindow('0050-01-01', '0050-01-31', 'year')).toEqual({
      start: '0049-01-01',
      end: '0049-01-31',
      clamped: false,
    })
  })
})
