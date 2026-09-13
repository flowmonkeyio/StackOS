import { beforeEach, describe, expect, it, vi } from 'vitest'
import { callOperation } from '@/lib/operations'
import { desktop } from '@/lib/desktop'
import { useHomeAgentHostStatuses } from './useHomeAgentHostStatuses'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
vi.mock('@/lib/desktop', () => ({ desktop: { hostStatuses: vi.fn() } }))
beforeEach(() => {
  vi.mocked(callOperation).mockReset()
  vi.mocked(desktop.hostStatuses).mockReset()
})

describe('home host status transport', () => {
  it('uses real browser operation data and retains last checked rows when refresh fails', async () => {
    const hosts = [
      { host_key: 'codex', status: 'registered_current', connection_state: 'connected' },
    ]
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

  it('preserves native IPC transport in Electron without duplicate browser reads', async () => {
    vi.mocked(desktop.hostStatuses).mockResolvedValue({ ok: true, items: [] })
    const state = useHomeAgentHostStatuses(true)
    await state.loadHostStatuses()
    expect(desktop.hostStatuses).toHaveBeenCalledTimes(1)
    expect(callOperation).not.toHaveBeenCalled()
  })
})
