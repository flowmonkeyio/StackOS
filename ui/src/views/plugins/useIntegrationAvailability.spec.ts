// @vitest-environment node

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { callOperation } from '@/lib/operations'
import { useIntegrationAvailability } from './useIntegrationAvailability'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))

const item = {
  plugin_slug: 'utils', provider_key: 'firecrawl', state: 'not_connected',
  executable_action_count: 0, total_action_count: 4, hidden_action_count: 1,
}

describe('feature-local integration availability', () => {
  beforeEach(() => vi.resetAllMocks())

  it('requests canonical unavailable inventory and preserves server counts and states', async () => {
    const pending = deferred<unknown>()
    vi.mocked(callOperation).mockReturnValueOnce(pending.promise)
    const inventory = useIntegrationAvailability()
    const refresh = inventory.refresh(7)
    expect(inventory.loading.value).toBe(true)
    expect(inventory.items.value).toEqual([])
    expect(callOperation).toHaveBeenCalledWith('integration.list', {
      project_id: 7, include_unavailable: true,
    })
    pending.resolve({ project_id: 7, items: [item] })
    await refresh
    expect(inventory.items.value).toEqual([item])
    expect(inventory.loading.value).toBe(false)
    expect(inventory.error.value).toBe(false)
  })

  it('distinguishes a valid empty inventory from unavailable or wrong-project results', async () => {
    vi.mocked(callOperation)
      .mockResolvedValueOnce({ project_id: 1, items: [] })
      .mockRejectedValueOnce(new Error('unavailable'))
      .mockResolvedValueOnce({ project_id: 2, items: [item] })
    const inventory = useIntegrationAvailability()
    await inventory.refresh(1)
    expect(inventory.items.value).toEqual([])
    expect(inventory.error.value).toBe(false)
    await inventory.refresh(1)
    expect(inventory.error.value).toBe(true)
    await inventory.refresh(1)
    expect(inventory.error.value).toBe(true)
    expect(inventory.items.value).toEqual([])
    expect(inventory.loading.value).toBe(false)
  })

  it.each([1, 2])('keeps the latest refresh when an older project %i request completes later', async (oldProject) => {
    const old = deferred<unknown>()
    const current = deferred<unknown>()
    vi.mocked(callOperation).mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise)
    const inventory = useIntegrationAvailability()
    const first = inventory.refresh(oldProject)
    const latest = inventory.refresh(1)
    current.resolve({ project_id: 1, items: [{ ...item, state: 'connected' }] })
    await latest
    old.resolve({ project_id: oldProject, items: [item] })
    await first
    expect(inventory.items.value[0]?.state).toBe('connected')
    expect(inventory.error.value).toBe(false)
    expect(inventory.loading.value).toBe(false)
  })

  it('does not finish a new request or show stale errors when an old request fails', async () => {
    const old = deferred<unknown>()
    const current = deferred<unknown>()
    vi.mocked(callOperation).mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise)
    const inventory = useIntegrationAvailability()
    const first = inventory.refresh(1)
    const latest = inventory.refresh(1)
    old.reject(new Error('stale failure'))
    await first
    expect(inventory.loading.value).toBe(true)
    expect(inventory.error.value).toBe(false)
    current.resolve({ project_id: 1, items: [item] })
    await latest
    expect(inventory.items.value).toEqual([item])
  })

  it('invalidates outstanding requests when the project route exits', async () => {
    const old = deferred<unknown>()
    vi.mocked(callOperation).mockReturnValueOnce(old.promise)
    const inventory = useIntegrationAvailability()
    const request = inventory.refresh(1)
    inventory.reset()
    old.resolve({ project_id: 1, items: [item] })
    await request
    expect(inventory.items.value).toEqual([])
    expect(inventory.loading.value).toBe(false)
    expect(inventory.error.value).toBe(false)
  })
})

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((accept, decline) => { resolve = accept; reject = decline })
  return { promise, resolve, reject }
}
