import { describe, expect, it } from 'vitest'
import {
  decimalToMinor,
  formatMoney,
  minorToDecimal,
  timeframe,
  comparisonWindow,
  canReadDocument,
  documentKind,
  documentName,
  documentSummaryText,
  documentCountsText,
  documentGroups,
  documentAvailabilityText,
} from './finance'

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

describe('UTC calendar periods', () => {
  it.each([
    ['week', '2024-12-31T12:00:00Z', '2024-12-30', '2025-01-05'],
    ['week', '2025-01-01T12:00:00Z', '2024-12-30', '2025-01-05'],
    ['week', '2025-01-05T23:59:59Z', '2024-12-30', '2025-01-05'],
    ['week', '2025-01-06T00:00:00Z', '2025-01-06', '2025-01-12'],
    ['week', '2026-10-14T12:00:00Z', '2026-10-12', '2026-10-18'],
    ['month', '2024-02-29T12:00:00Z', '2024-02-01', '2024-02-29'],
    ['month', '2023-02-28T12:00:00Z', '2023-02-01', '2023-02-28'],
    ['month', '2026-12-31T23:59:59Z', '2026-12-01', '2026-12-31'],
    ['month', '2027-01-01T00:00:00Z', '2027-01-01', '2027-01-31'],
    ['last-month', '2027-01-01T00:00:00Z', '2026-12-01', '2026-12-31'],
    ['last-month', '2024-03-01T00:00:00Z', '2024-02-01', '2024-02-29'],
    ['quarter', '2026-01-01T00:00:00Z', '2026-01-01', '2026-03-31'],
    ['quarter', '2026-03-31T23:59:59Z', '2026-01-01', '2026-03-31'],
    ['quarter', '2026-04-01T00:00:00Z', '2026-04-01', '2026-06-30'],
    ['quarter', '2026-06-30T23:59:59Z', '2026-04-01', '2026-06-30'],
    ['quarter', '2026-07-01T00:00:00Z', '2026-07-01', '2026-09-30'],
    ['quarter', '2026-09-30T23:59:59Z', '2026-07-01', '2026-09-30'],
    ['quarter', '2026-10-01T00:00:00Z', '2026-10-01', '2026-12-31'],
    ['quarter', '2026-12-31T23:59:59Z', '2026-10-01', '2026-12-31'],
    ['year', '2024-02-29T12:00:00Z', '2024-01-01', '2024-12-31'],
    ['year', '2027-01-01T00:00:00Z', '2027-01-01', '2027-12-31'],
    ['year', '2026-01-01T00:30:00+14:00', '2025-01-01', '2025-12-31'],
    ['week', '2026-01-01T00:30:00+14:00', '2025-12-29', '2026-01-04'],
  ])('resolves %s at %s to the complete calendar range', (preset, now, start, end) => {
    expect(timeframe(preset, new Date(now))).toEqual({ start, end })
  })
  it('keeps the complete calendar year distinct from year to date', () => {
    const now = new Date('2026-10-14T12:00:00Z')
    expect(timeframe('year', now)).toEqual({ start: '2026-01-01', end: '2026-12-31' })
    expect(timeframe('year-to-date', now)).toEqual({ start: '2026-01-01', end: '2026-10-14' })
    expect(comparisonWindow('2026-01-01', '2026-12-31', 'year')).toEqual({
      start: '2025-01-01',
      end: '2025-12-31',
      clamped: false,
    })
  })
})

describe('declared original document presentation', () => {
  it.each([
    ['available', 'Available · 2'],
    ['unavailable', 'Unavailable · 2'],
    ['not_verified', 'Not verified · 2'],
    ['partial', 'Partial · 1/2 available'],
    ['not_linked', 'Not linked'],
    ['unidentified', 'Needs identification'],
  ])('formats %s without deriving financial status', (state, label) => {
    const count = ['not_linked', 'unidentified'].includes(state) ? 0 : 2
    const summary = {
      state,
      document_count: count,
      available_count: 1,
      unavailable_count: 1,
      not_verified_count: 0,
    }
    expect(documentSummaryText(summary)).toBe(label)
    if (count)
      expect(documentCountsText(summary)).toBe(
        '2 linked · 1 available · 1 unavailable · 0 not verified',
      )
  })
  it('does not infer kind or availability from a filename, collection, role or legacy state', () => {
    const legacy = {
      attachment_ref: 'attachment:legacy',
      filename: 'paid-invoice.pdf',
      state: 'retained',
      role: 'receipt-original',
    }
    expect(documentKind(legacy)).toBe('unidentified')
    expect(documentKind('purchase-invoice')).toBe('unidentified')
    expect(documentSummaryText(null)).toBe('Not verified')
    expect(documentCountsText(null)).toContain('has not been verified')
    expect(documentAvailabilityText(legacy)).toContain('Not verified')
    expect(canReadDocument(legacy)).toBe(true)
    expect(documentName({})).toBe('Unnamed original')
    const groups = documentGroups([legacy], null)
    expect(groups.map((group) => group.documents.length)).toEqual([0, 0, 1])
    expect(groups[0]!.status).toBe('Not verified')
    expect(groups[2]!.status).toBe('Needs identification')
    expect(documentGroups([], null)[2]!.status).toBe('None listed')
  })
  it('keeps typed originals and unknown files separate, with explicit counts', () => {
    const documents = [
      { attachment_ref: 'invoice:one', document_type: 'invoice' },
      { attachment_ref: 'receipt:one', document_type: 'receipt' },
      { attachment_ref: 'other:one', document_type: 'unidentified' },
    ]
    expect(
      documentGroups(documents, {}).map((group) => group.documents[0]?.attachment_ref),
    ).toEqual(['invoice:one', 'receipt:one', 'other:one'])
    expect(documentCountsText({ state: 'not_linked', document_count: 0 })).toBe(
      'No original of this type is linked.',
    )
    expect(documentCountsText({ state: 'unidentified', document_count: 0 })).toContain(
      'need identification',
    )
  })
  it.each(['unavailable', 'quarantined', 'disposed', 'missing', 'changed', 'unreadable'])(
    'disables %s without fabricating a missing cause',
    (state) => {
      expect(canReadDocument({ attachment_ref: 'one', state })).toBe(false)
      expect(canReadDocument({ attachment_ref: 'one', retention_state: 'retained', state })).toBe(
        false,
      )
      expect(
        canReadDocument({ attachment_ref: 'one', availability: 'unavailable', state: 'retained' }),
      ).toBe(false)
      expect(
        documentAvailabilityText({ availability: 'unavailable', reason: state }),
      ).not.toContain('Missing')
    },
  )
})
