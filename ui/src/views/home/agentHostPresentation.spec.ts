import { describe, expect, it } from 'vitest'

import type { DesktopMcpHostStatus } from '@/lib/desktop'

import {
  SUPPORTED_AGENT_HOSTS,
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
    ...overrides,
  }
}

describe('agent host presentation', () => {
  it('includes Hermes in the visible local-agent connection list', () => {
    expect(SUPPORTED_AGENT_HOSTS).toContainEqual({ host_key: 'hermes', label: 'Hermes' })
  })

  it('describes the StackOS connection instead of claiming a host is installed', () => {
    expect(agentHostPresentation(host()).label).toBe('Connected')
    expect(
      agentHostPresentation(host({ status: 'available_unregistered', ok: false })).label,
    ).toBe('Not connected')
    expect(agentHostPresentation(host({ status: 'absent', available: false })).label).toBe(
      'Not detected',
    )
  })

  it('summarizes stable connection states in plain language', () => {
    expect(
      agentHostSummary([
        host(),
        host({ host_key: 'claude-code', status: 'restart_required', needs_restart: true }),
        host({ host_key: 'claude-desktop', status: 'absent', available: false }),
      ]),
    ).toBe('1 connected · 1 needs attention · 1 not detected')
  })
})
