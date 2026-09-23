import { describe, expect, it } from 'vitest'

import {
  applyProviderResultsToIngressStatus,
  discoveryFailureMessage,
  summarizeProviderResults,
} from './ingressResults'

describe('ingressResults', () => {
  it('summarizes manual provider updates', () => {
    expect(
      summarizeProviderResults([
        { provider_key: 'slack-bot', status: 'manual_provider_update_required' },
      ]),
    ).toEqual({
      tone: 'info',
      text: 'Slack needs manual webhook update.',
    })
  })

  it('surfaces failed and skipped states without claiming success', () => {
    expect(
      summarizeProviderResults([
        { provider_key: 'slack-bot', status: 'failed', error: '[redacted]' },
        { provider_key: 'other', status: 'skipped' },
      ]),
    ).toEqual({
      tone: 'danger',
      text: '1 provider skipped. 1 provider sync failed. [redacted]',
    })
  })

  it('requires a discovered public address before reporting local tunnel success', () => {
    expect(discoveryFailureMessage({ status: 'failed', public_base_url: null })).toContain(
      'No public address was discovered',
    )
    expect(
      discoveryFailureMessage({
        status: 'running',
        public_base_url: 'https://example.ngrok-free.app',
      }),
    ).toBeNull()
  })

  it('overlays the latest provider sync result onto the matching route only', () => {
    const status = applyProviderResultsToIngressStatus(
      {
        configured: true,
        ready: true,
        routes: [
          {
            provider_key: 'slack-bot',
            profile_key: 'revtrix-slack',
            ingress_url: 'https://example.com/api/v1/ingress/slack/1/revtrix-slack',
            remote_status: 'manual_provider_update_required',
          },
        ],
      },
      [
        {
          provider_key: 'slack-bot',
          profile_key: 'revtrix-slack',
          status: 'manual_provider_update_required',
          request_url: 'https://other.example.com/api/v1/ingress/slack/1/revtrix-slack',
        },
      ],
    )

    expect(status?.routes?.[0]?.remote_status).toBe('manual_provider_update_required')
  })
})
