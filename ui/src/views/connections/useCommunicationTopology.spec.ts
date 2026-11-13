import { afterEach, describe, expect, it, vi } from 'vitest'
import { computed, defineComponent, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { callOperation } from '@/lib/operations'
import { useCommunicationTopology } from './useCommunicationTopology'
import { useIngressEndpointEditor } from './useIngressEndpointEditor'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))
afterEach(() => vi.mocked(callOperation).mockReset())
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (cause: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

describe('Connections compatible read lifetimes', () => {
  it.each([true, false])('retains all four collections through pending and partial failure (failure first: %s)', async failureFirst => {
    let topology!: ReturnType<typeof useCommunicationTopology>
    const id = ref(1)
    const wrapper = mount(defineComponent({ setup() {
      topology = useCommunicationTopology(computed(() => id.value))
      return () => null
    } }))
    try {
      vi.mocked(callOperation).mockResolvedValue({ items: [{ key: 'accepted' }] })
      await topology.load()
      const pending = deferred<never>()
      const success = deferred<never>()
      vi.mocked(callOperation).mockImplementation(name => name === 'communicationTarget.list' ? pending.promise : success.promise)
      const read = topology.load()
      expect(topology.loading.value).toBe(true)
      for (const rows of [topology.profiles, topology.targets, topology.surfaces, topology.routes])
        expect(rows.value[0]).toMatchObject({ key: 'accepted' })
      if (failureFirst) {
        pending.reject(new Error('Target offline'))
        await flushPromises()
        expect(topology.loading.value).toBe(true)
        success.resolve({ items: [{ key: 'updated' }] } as never)
      } else {
        success.resolve({ items: [{ key: 'updated' }] } as never)
        await flushPromises()
        expect(topology.loading.value).toBe(true)
        pending.reject(new Error('Target offline'))
      }
      await read
      expect(topology.profiles.value[0]?.key).toBe('updated')
      expect(topology.targets.value[0]?.key).toBe('accepted')
      expect(topology.message.value?.text).toContain('Target offline')
      expect(topology.loaded.value).toBe(true)

      const late = deferred<never>()
      vi.mocked(callOperation).mockReturnValue(late.promise)
      const oldRead = topology.load()
      id.value = 2
      vi.mocked(callOperation).mockResolvedValue({ items: [] })
      const newRead = topology.load()
      expect(topology.profiles.value).toEqual([])
      await newRead
      late.resolve({ items: [{ key: 'obsolete' }] } as never)
      await oldRead
      expect(topology.profiles.value).toEqual([])
      const afterExit = deferred<never>()
      vi.mocked(callOperation).mockReturnValue(afterExit.promise)
      const retired = topology.load()
      wrapper.unmount()
      afterExit.resolve({ items: [{ key: 'retired' }] } as never)
      await retired
      expect(topology.profiles.value).toEqual([])
    } finally { if (wrapper.exists()) wrapper.unmount() }
  })

  it('retains ingress through failed refresh and suppresses scope-exit results', async () => {
    let ingress!: ReturnType<typeof useIngressEndpointEditor>
    const id = ref(1)
    const wrapper = mount(defineComponent({ setup() {
      ingress = useIngressEndpointEditor({ projectId: computed(() => id.value), busyAction: ref(null), onTopologyChanged: vi.fn() })
      return () => null
    } }))
    vi.mocked(callOperation).mockResolvedValue({ endpoint: null, routes: [], ready: false })
    await ingress.load()
    const accepted = ingress.status.value
    const read = deferred<never>()
    vi.mocked(callOperation).mockReturnValue(read.promise)
    const refresh = ingress.load()
    expect(ingress.status.value).toEqual(accepted)
    read.reject(new Error('Ingress offline'))
    await refresh
    expect(ingress.status.value).toEqual(accepted)
    expect(ingress.loadMessage.value?.text).toContain('Ingress offline')
    const late = deferred<never>()
    vi.mocked(callOperation).mockReturnValue(late.promise)
    const pending = ingress.load()
    wrapper.unmount()
    late.resolve({ endpoint: { key: 'obsolete' }, routes: [] } as never)
    await pending
    expect(ingress.status.value).toEqual(accepted)
  })
})
