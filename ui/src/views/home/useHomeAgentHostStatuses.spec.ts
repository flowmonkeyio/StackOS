import { beforeEach, describe, expect, it, vi } from 'vitest'
import { callOperation } from '@/lib/operations'
import { desktop, type DesktopMcpHostStatus } from '@/lib/desktop'
import { useHomeAgentHostStatuses } from './useHomeAgentHostStatuses'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
vi.mock('@/lib/desktop', () => ({ desktop: { hostStatuses: vi.fn() } }))
beforeEach(() => {
  vi.mocked(callOperation).mockReset()
  vi.mocked(desktop.hostStatuses).mockReset()
})

const catalogHost: DesktopMcpHostStatus = {
  host_key: 'codex',
  status: 'registered_current',
  connection_state: 'connected',
  agent_execution: {
    sandboxes: ['workspace-write', 'read-only'],
    review_sandbox: 'workspace-write',
    models: ['fixture-model'],
    custom_models: false,
    provider_selection: true,
    profile_selection: false,
    requires_provider_model: false,
    notes: 'Fixture catalog; access is not checked.',
    sources: ['https://example.com/catalog'],
    checked_on: '2026-11-05',
    credential_status: 'not_checked',
    model_access: 'not_checked',
  },
}

describe('home host status transport', () => {
  it('uses real browser operation data and retains last checked rows when refresh fails', async () => {
    const hosts = [catalogHost]
    vi.mocked(callOperation).mockResolvedValue({ ok: true, items: hosts })
    const state = useHomeAgentHostStatuses(false)
    await state.loadHostStatuses()
    expect(callOperation).toHaveBeenCalledWith('hostMcp.status', {})
    expect(desktop.hostStatuses).not.toHaveBeenCalled()
    expect(state.hostStatuses.value).toEqual({ kind: 'loaded', items: hosts })
    vi.mocked(callOperation).mockRejectedValue(new Error('Service unavailable'))
    await state.loadHostStatuses()
    expect(state.hostStatuses.value).toEqual({
      kind: 'error',
      items: hosts,
      message: 'Service unavailable',
    })
  })

  it.each([catalogHost, { host_key: 'codex' }])(
    'preserves native IPC rows with or without a catalog, without duplicate browser reads',
    async (host) => {
      vi.mocked(desktop.hostStatuses).mockResolvedValue({ ok: true, items: [host] })
      const state = useHomeAgentHostStatuses(true)
      await state.loadHostStatuses()
      expect(state.hostStatuses.value).toEqual({ kind: 'loaded', items: [host] })
      expect(desktop.hostStatuses).toHaveBeenCalledTimes(1)
      expect(callOperation).not.toHaveBeenCalled()
    },
  )
})
