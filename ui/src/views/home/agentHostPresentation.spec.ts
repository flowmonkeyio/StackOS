import { describe, expect, it } from 'vitest'

import type { DesktopMcpHostStatus } from '@/lib/desktop'

import {
  agentHostLogo,
  agentHostPresentation,
  agentHostSummary,
} from './agentHostPresentation'

function host(overrides: Partial<DesktopMcpHostStatus> = {}): DesktopMcpHostStatus {
  return {
    host_key: 'codex',
    status: 'registered_current',
    ok: true,
    available: true,
    advisory: false,
    blocking: false,
    needs_restart: false,
    display_name: 'ChatGPT / Codex',
    connection_state: 'connected',
    status_label: 'Connected',
    message: 'StackOS is ready in ChatGPT and Codex.',
    ...overrides,
  }
}

describe('agent host presentation', () => {
  it('uses the shared host logo assets', () => {
    expect(agentHostLogo(host())).toBe('/images/openai.webp')
    expect(agentHostLogo(host({ host_key: 'claude-code' }))).toBe('/images/claude-code.webp')
    expect(agentHostLogo(host({ host_key: 'claude-desktop' }))).toBe('/images/claude.webp')
    expect(agentHostLogo(host({ host_key: 'gemini-cli' }))).toBe('/images/gemini.webp')
    expect(agentHostLogo(host({ host_key: 'hermes' }))).toBe('/images/hermes.webp')
  })

  it('describes the StackOS connection instead of claiming a host is installed', () => {
    expect(agentHostPresentation(host()).label).toBe('Connected')
    expect(
      agentHostPresentation(
        host({
          status: 'available_unregistered',
          connection_state: 'available',
          status_label: 'Available',
          ok: true,
        }),
      ).label,
    ).toBe('Available')
    expect(agentHostPresentation(host({
      status: 'absent',
      connection_state: 'unavailable',
      status_label: 'Not detected',
      available: false,
    })).label).toBe(
      'Not detected',
    )
  })

  it('summarizes stable connection states in plain language', () => {
    expect(
      agentHostSummary([
        host({
          display_name: 'ChatGPT / Codex',
          connection_state: 'connected',
          status_label: 'Connected',
          message: 'StackOS is ready in ChatGPT and Codex.',
        }),
        host({
          host_key: 'claude-code',
          connection_state: 'available',
          status_label: 'Available',
          message: 'Claude Code is available to connect.',
        }),
        host({
          host_key: 'claude-desktop',
          connection_state: 'repair_needed',
          status_label: 'Repair needed',
          message: 'The StackOS-owned connection is stale.',
          blocking: true,
        }),
        host({
          host_key: 'hermes',
          connection_state: 'unavailable',
          status_label: 'Not detected',
          message: 'Hermes is optional and was not detected.',
        }),
      ]),
    ).toBe('1 connected · 1 available · 1 needs attention · 1 not detected')
  })

  it('renders the backend-owned lifecycle instead of interpreting raw status', () => {
    const presentation = agentHostPresentation(
      host({
        status: 'unexpected_future_status',
        connection_state: 'review_required',
        status_label: 'Review required',
        message: 'This name is owned by another connection. StackOS left it unchanged.',
      }),
    )

    expect(presentation).toEqual({
      state: 'review_required',
      label: 'Review required',
      detail: 'This name is owned by another connection. StackOS left it unchanged.',
      tone: 'danger',
    })
  })
})
