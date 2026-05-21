import { describe, expect, it } from 'vitest'

import { sanitizeForDisplay } from './json'

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
