import { describe, expect, it } from 'vitest'

import { formatCount, formatPercent, formatUsd } from './format'

describe('formatUsd', () => {
  it('formats currency to two decimals', () => {
    expect(formatUsd(1240)).toBe('$1,240.00')
    expect(formatUsd(0)).toBe('$0.00')
    expect(formatUsd(3.5)).toBe('$3.50')
  })
  it('treats null/NaN as zero', () => {
    expect(formatUsd(null)).toBe('$0.00')
    expect(formatUsd(undefined)).toBe('$0.00')
    expect(formatUsd(Number.NaN)).toBe('$0.00')
  })
})

describe('formatPercent', () => {
  it('rounds to whole percent by default', () => {
    expect(formatPercent(83.4)).toBe('83%')
    expect(formatPercent(100)).toBe('100%')
    expect(formatPercent(12.5, 1)).toBe('12.5%')
  })
})

describe('formatCount', () => {
  it('groups thousands', () => {
    expect(formatCount(1234)).toBe('1,234')
    expect(formatCount(null)).toBe('0')
  })
})
