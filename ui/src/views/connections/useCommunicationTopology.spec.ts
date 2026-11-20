import { afterEach, describe, expect, it, vi } from 'vitest'
import { computed, defineComponent, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { callOperation } from '@/lib/operations'
import { ApiError } from '@/lib/client'
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
  it.each([true, false])('keeps initial successful empty siblings known through partial failure (failure first: %s)', async failureFirst => {
    let topology!: ReturnType<typeof useCommunicationTopology>
    const wrapper = mount(defineComponent({ setup() {
      topology = useCommunicationTopology(computed(() => 1))
      return () => null
    } }))
    try {
      const target = deferred<never>(), siblings = deferred<never>()
      vi.mocked(callOperation).mockImplementation(name => name === 'communicationTarget.list' ? target.promise : siblings.promise)
      const read = topology.load()
      if (failureFirst) {
        target.reject(new Error('Target offline'))
        await flushPromises()
        expect(topology.states.value.targets.loading).toBe(false)
        expect(topology.states.value.profiles.loading).toBe(true)
        siblings.resolve({ items: [] } as never)
      } else {
        siblings.resolve({ items: [] } as never)
        await flushPromises()
        expect(topology.states.value.profiles.loaded).toBe(true)
        expect(topology.states.value.targets.loading).toBe(true)
        target.reject(new Error('Target offline'))
      }
      await read
      expect(topology.states.value.targets.loaded).toBe(false)
      for (const key of ['profiles', 'surfaces', 'routes'] as const) {
        expect(topology.states.value[key]).toEqual({ loaded: true, loading: false, message: null })
      }
      vi.mocked(callOperation).mockClear().mockResolvedValue({ items: [] })
      await topology.load('targets')
      expect(callOperation).toHaveBeenCalledOnce()
      expect(callOperation).toHaveBeenCalledWith('communicationTarget.list', { project_id: 1, limit: 50 })
      expect(topology.loaded.value).toBe(true)
      expect(topology.message.value).toBeNull()
    } finally { wrapper.unmount() }
  })

  it('clears only denied topology sources and ignores an older result after denial', async () => {
    let topology!: ReturnType<typeof useCommunicationTopology>
    const wrapper = mount(defineComponent({ setup() {
      topology = useCommunicationTopology(computed(() => 1))
      return () => null
    } }))
    try {
      vi.mocked(callOperation).mockResolvedValue({ items: [{ key: 'accepted' }] })
      await topology.load()
      const old = deferred<never>()
      vi.mocked(callOperation).mockReturnValueOnce(old.promise)
      const pending = topology.load('profiles')
      vi.mocked(callOperation).mockRejectedValue(new ApiError('Permission revoked', 403, {}))
      await topology.load('profiles')
      old.resolve({ items: [{ key: 'obsolete' }] } as never)
      await pending
      expect(topology.profiles.value).toEqual([])
      expect(topology.states.value.profiles.loaded).toBe(false)
      expect(topology.states.value.profiles.message?.text).toContain('Permission revoked')
      expect(topology.targets.value[0]?.key).toBe('accepted')
      expect(topology.states.value.targets.loaded).toBe(true)
    } finally { wrapper.unmount() }
  })
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

  it('clears denied ingress and rejects a late earlier read without losing its error', async () => {
    let ingress!: ReturnType<typeof useIngressEndpointEditor>
    const wrapper = mount(defineComponent({ setup() {
      ingress = useIngressEndpointEditor({ projectId: computed(() => 1), busyAction: ref(null), onTopologyChanged: vi.fn() })
      return () => null
    } }))
    try {
      vi.mocked(callOperation).mockResolvedValue({ endpoint: { key: 'private-A' }, routes: [], ready: true })
      await ingress.load()
      ingress.openPanel()
      const old = deferred<never>()
      vi.mocked(callOperation).mockReturnValueOnce(old.promise)
      const pending = ingress.load()
      vi.mocked(callOperation).mockRejectedValue(new ApiError('Permission revoked', 403, {}))
      await ingress.load()
      old.resolve({ endpoint: { key: 'obsolete' }, routes: [], ready: true } as never)
      await pending
      expect(ingress.status.value).toBeNull()
      expect(ingress.panelOpen.value).toBe(false)
      expect(ingress.loading.value).toBe(false)
      expect(ingress.loadMessage.value?.text).toContain('Permission revoked')
    } finally { wrapper.unmount() }
  })
})
