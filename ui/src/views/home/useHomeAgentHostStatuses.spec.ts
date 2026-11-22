import { beforeEach, describe, expect, it, vi } from 'vitest'
import { callOperation } from '@/lib/operations'
import { desktop, type DesktopMcpHostStatus } from '@/lib/desktop'
import { useHomeAgentHostStatuses } from './useHomeAgentHostStatuses'
import { defineComponent, h, onMounted, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

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
  it('keeps the latest refresh when older success or failure finishes late', async () => {
    let finish!: (value: unknown) => void
    let fail!: (error: Error) => void
    vi.mocked(callOperation).mockImplementationOnce(() => new Promise((resolve, reject) => { finish = resolve; fail = reject }))
      .mockResolvedValue({ items: [{ ...catalogHost, message: 'Current native capability' }] })
    const state = useHomeAgentHostStatuses(false)
    const old = state.loadHostStatuses()
    await state.loadHostStatuses()
    finish({ items: [{ ...catalogHost, message: 'Old account capability' }] })
    await old
    expect(state.hostStatuses.value.items[0]?.message).toBe('Current native capability')
    vi.mocked(callOperation).mockImplementationOnce(() => new Promise((_resolve, reject) => { fail = reject }))
      .mockResolvedValue({ items: [{ ...catalogHost, message: 'New current capability' }] })
    const older = state.loadHostStatuses()
    await state.loadHostStatuses()
    fail(new Error('Retired refresh failed')); await older
    expect(state.hostStatuses.value.kind).toBe('loaded')
    expect(state.hostStatuses.value.items[0]?.message).toBe('New current capability')
  })
  it('never replaces retained current connections with a retired reader result', async () => {
    const visible = ref(true)
    let state!: ReturnType<typeof useHomeAgentHostStatuses>
    let finish!: (value: unknown) => void
    vi.mocked(callOperation).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
      .mockResolvedValue({ ok: true, items: [{ ...catalogHost, message: 'Current connection' }] })
    const Reader = defineComponent({ setup() { state = useHomeAgentHostStatuses(false); onMounted(state.loadHostStatuses); return () => null } })
    const wrapper = mount(defineComponent({ setup: () => () => visible.value ? h(Reader) : null }))
    try {
      visible.value = false; await flushPromises()
      visible.value = true; await flushPromises()
      finish({ ok: true, items: [{ ...catalogHost, message: 'Retired connection' }] }); await flushPromises()
      visible.value = false; await flushPromises()
      vi.mocked(callOperation).mockImplementation(() => new Promise(() => {}))
      visible.value = true; await flushPromises()
      expect(state.hostStatuses.value.items[0]?.message).toBe('Current connection')
      expect(state.hostStatusSummary.value).not.toContain('Refreshing')
    } finally { wrapper.unmount() }
  })
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
