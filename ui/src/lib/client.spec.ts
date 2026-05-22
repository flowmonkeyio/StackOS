import { describe, expect, it } from 'vitest'

import { ApiError, formatApiError } from './client'

describe('client error formatting', () => {
  it('uses backend error detail and hint from the standard envelope', () => {
    const err = new ApiError('fallback', 422, {
      detail: 'api_key is required to rotate a credential',
      code: -32602,
      retryable: false,
      hint: 'Paste the secret again before saving.',
    })

    expect(err.detail).toBe('api_key is required to rotate a credential')
    expect(formatApiError(err)).toContain('Paste the secret again before saving.')
  })

  it('unwraps FastAPI HTTPException detail envelopes', () => {
    const err = new ApiError('fallback', 422, {
        detail: {
        detail: 'dataforseo credential missing login',
        code: -32602,
      },
    })

    expect(formatApiError(err)).toBe('dataforseo credential missing login')
  })

  it('summarizes budget failure data for operator toasts', () => {
    const err = new ApiError('fallback', 402, {
      detail: 'openai-images budget would exceed cap',
      code: -32012,
      retryable: false,
      data: {
        monthly_budget_usd: 50,
        current_month_spend: 49.25,
        attempted_cost_usd: 1.5,
      },
    })

    expect(formatApiError(err)).toContain('$49.25 spent + $1.50 attempted of $50.00 cap')
  })
})
