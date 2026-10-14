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
