import { createPinia, setActivePinia } from 'pinia'
import { expect, it, vi } from 'vitest'

import { callOperation } from '@/lib/operations'
import { useHomeSubscriptions } from './useHomeSubscriptions'

vi.mock('@/lib/operations', () => ({ callOperation: vi.fn() }))

it('shares a pending read, reuses a recent snapshot and clears readings on a failed refresh', async () => {
  setActivePinia(createPinia())
  const store = useHomeSubscriptions()
  const snapshot = { checked_at: new Date().toISOString(), readings: { grok: null } }
  let resolve!: (value: typeof snapshot) => void
  vi.mocked(callOperation).mockReturnValueOnce(new Promise(done => { resolve = done }))
  const entered = store.load()
  const refreshed = store.load(true)
  expect(callOperation).toHaveBeenCalledTimes(1)
  resolve(snapshot)
  await Promise.all([entered, refreshed])
  expect(store.subscriptions).toEqual({ ...snapshot, loading: false, failed: false })
  await useHomeSubscriptions().load()
  expect(callOperation).toHaveBeenCalledTimes(1)
  vi.mocked(callOperation).mockRejectedValueOnce(new Error('offline'))
  await store.load(true)
  expect(store.subscriptions).toEqual({ checked_at: null, readings: {}, loading: false, failed: true })
})
