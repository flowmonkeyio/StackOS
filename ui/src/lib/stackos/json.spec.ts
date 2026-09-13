import { describe, expect, it } from 'vitest'

import { formatDateTime, sanitizeForDisplay } from './json'
import { formatAbsoluteDateTime } from './time'

describe('formatDateTime', () => {
  it.each([
    ['2026-09-07T16:18:00', '2026-09-07T16:18:00Z'],
    ['2026-09-07T16:18:00.123456', '2026-09-07T16:18:00.123Z'],
    ['2026-09-07T16:18:00Z', '2026-09-07T16:18:00Z'],
    ['2026-09-07T18:18:00+02:00', '2026-09-07T16:18:00Z'],
  ])('uses the shared UTC interpretation for %s', (value, instant) => {
    expect(formatDateTime(value)).toBe(new Date(instant).toLocaleString())
    expect(formatDateTime(value)).toBe(formatAbsoluteDateTime(value))
  })

  it.each([null, undefined, ''])('keeps the existing missing-value placeholder for %s', (value) => {
    expect(formatDateTime(value)).toBe('-')
  })

  it('preserves invalid text for inspection', () => {
    expect(formatDateTime('not-a-date')).toBe('not-a-date')
  })
})

describe('sanitizeForDisplay', () => {
  it('redacts nested secret-looking fields while preserving opaque refs', () => {
    const out = sanitizeForDisplay({
      api_key: 'secret',
      credential_ref: 'cred_123',
      nested: {
        authorization: 'Bearer abc',
        note: 'token=abc',
      },
    })

    expect(out).toEqual({
      api_key: '[redacted]',
      credential_ref: 'cred_123',
      nested: {
        authorization: '[redacted]',
        note: 'token=[redacted]',
      },
    })
  })
})
