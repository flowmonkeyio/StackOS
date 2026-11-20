// @vitest-environment node

import { describe, expect, it } from 'vitest'
import { resolveStatus } from './status'

describe('canonical integration availability statuses', () => {
  it.each([
    ['provider_disabled', 'Provider disabled', 'neutral'],
    ['connected_blocked', 'Connected · actions blocked', 'warning'],
    ['connected', 'Connected', 'success'],
    ['repair_required', 'Account repair required', 'danger'],
    ['partially_available', 'Partially available', 'neutral'],
    ['not_connected', 'Not connected', 'neutral'],
    ['ready_no_connection_required', 'Ready · no connection required', 'success'],
    ['available', 'Available', 'neutral'],
  ])('renders %s distinctly without treating unused optional providers as warnings', (state, label, tone) => {
    expect(resolveStatus('integration', state)).toMatchObject({ label, tone })
  })
})
