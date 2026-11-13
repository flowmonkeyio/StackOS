import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { computed, defineComponent, h, ref } from 'vue'

import { ApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'

import ConnectivityPanel from './ConnectivityPanel.vue'
import type { IngressEndpointStatusOut } from './types'
import { useIngressEndpointEditor } from './useIngressEndpointEditor'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))

const accepted: IngressEndpointStatusOut = {
  ready: true,
  endpoint: { driver: 'public-url', public_base_url: 'https://accepted.invalid', status: 'ready' },
  routes: [],
}
const manualRoute = { provider_key: 'slack-bot', profile_key: 'fixture', ingress_url: 'https://accepted.invalid/hook' }
const owners: Array<ReturnType<typeof mount>> = []
function consumer() {
  const projectId = ref(1)
  const busyAction = ref<string | null>(null)
  const onTopologyChanged = vi.fn(async () => {})
  let state!: ReturnType<typeof useIngressEndpointEditor>
  const wrapper = mount(defineComponent({ setup() {
    state = useIngressEndpointEditor({ projectId: computed(() => projectId.value), busyAction, onTopologyChanged })
    return () => h(ConnectivityPanel, {
      ingressStatus: state.status.value,
      loading: state.loading.value,
      syncing: busyAction.value === 'ingress:sync',
      message: state.message.value,
    })
  } }))
  owners.push(wrapper)
  return { state, projectId, busyAction, onTopologyChanged, wrapper }
}
function deferred() {
  let resolve!: (value: never) => void
  const promise = new Promise<never>(yes => { resolve = yes })
  return { promise, resolve }
}
afterEach(() => {
  owners.splice(0).forEach(wrapper => wrapper.unmount())
  vi.resetAllMocks()
})

describe('ingress mutation authority and readback presentation', () => {
  const actions = ['configure', 'sync', 'confirmManualUpdate'] as const
  for (const action of actions) {
    it.each(['denial', 'scope exit', 'unmount'] as const)(`fences late ${action} after %s`, async transition => {
      const { state, projectId, busyAction, onTopologyChanged, wrapper } = consumer()
      vi.mocked(callOperation).mockResolvedValue(accepted)
      await state.load()
      const pending = deferred()
      vi.mocked(callOperation).mockReturnValueOnce(pending.promise)
      // Local-tunnel configure has a second write, which must also stay fenced.
      state.form.value.driver = 'local-tunnel'
      const write = action === 'configure' ? state.save() : state.sync(action === 'confirmManualUpdate' ? manualRoute : undefined)
      let expected = accepted
      if (transition === 'denial') {
        vi.mocked(callOperation).mockRejectedValueOnce(new ApiError('Permission revoked', 403, {}))
        await state.load()
      } else if (transition === 'scope exit') {
        projectId.value = 2
        expected = { ...accepted, endpoint: { public_base_url: 'https://project-b.invalid' } }
        vi.mocked(callOperation).mockResolvedValueOnce(expected)
        await state.load()
      } else wrapper.unmount()
      const calls = vi.mocked(callOperation).mock.calls.length
      const result = action === 'configure' ? accepted.endpoint : action === 'sync'
        ? { endpoint: accepted.endpoint, provider_results: [{ provider_key: 'slack-bot', status: 'updated' }] }
        : accepted
      pending.resolve({ data: result } as never)
      await write
      expect(state.status.value).toEqual(transition === 'denial' ? null : expected)
      expect(state.message.value?.text ?? '').not.toMatch(/configured|confirmed|updated\./)
      expect(onTopologyChanged).not.toHaveBeenCalled()
      expect(callOperation).toHaveBeenCalledTimes(calls)
      expect(busyAction.value).toBeNull()
      if (transition === 'denial') {
        expect(wrapper.text()).toContain('Permission revoked')
        vi.mocked(callOperation).mockResolvedValueOnce(accepted)
        await state.load()
        expect(state.status.value).toEqual(accepted)
        expect(state.message.value).toBeNull()
      }
    })
  }

  it('accepts a current confirmation and retires a read begun before it completed', async () => {
    const { state, onTopologyChanged } = consumer()
    vi.mocked(callOperation).mockResolvedValueOnce(accepted)
    await state.load()
    const oldRead = deferred()
    vi.mocked(callOperation).mockReturnValueOnce(oldRead.promise)
    const reading = state.load()
    const confirmed = { ...accepted, endpoint: { public_base_url: 'https://confirmed.invalid' } }
    vi.mocked(callOperation).mockResolvedValueOnce({ data: confirmed })
    await state.sync(manualRoute)
    oldRead.resolve(accepted as never)
    await reading
    expect(state.status.value).toEqual(confirmed)
    expect(state.message.value?.text).toContain('Slack webhook update confirmed')
    expect(state.loading.value).toBe(false)
    expect(onTopologyChanged).toHaveBeenCalledOnce()
  })

  for (const action of ['save', 'sync'] as const) {
    it.each([403, 503])(`shows ${action} outcome and failed %s readback until a successful retry`, async status => {
      const { state, wrapper } = consumer()
      vi.mocked(callOperation).mockResolvedValueOnce(accepted)
      await state.load()
      state.openPanel()
      const operation = action === 'save' ? 'ingressEndpoint.configure' : 'ingressEndpoint.sync'
      const outcome = action === 'save' ? 'Connectivity configured.' : 'No provider route updates were needed.'
      vi.mocked(callOperation).mockImplementation(name => name === operation
        ? Promise.resolve({ data: action === 'save' ? accepted.endpoint : { endpoint: accepted.endpoint, provider_results: [] } }) as never
        : Promise.reject(new ApiError('Readback unavailable', status, {})))
      await state[action]()
      expect(state.status.value).toEqual(status === 403 ? null : accepted)
      expect(wrapper.text()).toContain(outcome)
      expect(wrapper.text()).toContain('Readback unavailable')
      const retry = deferred()
      vi.mocked(callOperation).mockReturnValueOnce(retry.promise)
      const reading = state.load()
      expect(state.loadMessage.value?.text).toContain('Readback unavailable')
      expect(wrapper.text()).toContain('Readback unavailable')
      retry.resolve(accepted as never)
      await reading
      expect(state.status.value).toEqual(accepted)
      expect(state.loadMessage.value).toBeNull()
      expect(wrapper.text()).not.toContain('Readback unavailable')
      expect(wrapper.text()).toContain(outcome)
    })
  }
})
