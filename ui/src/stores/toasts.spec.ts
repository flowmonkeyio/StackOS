// @vitest-environment node

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useToastsStore } from './toasts'

describe('toasts store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('adds and dismisses toasts by id', () => {
    const toasts = useToastsStore()
    const id = toasts.success('saved')
    expect(toasts.items.length).toBe(1)
    expect(toasts.items[0].id).toBe(id)
    toasts.dismiss(id)
    expect(toasts.items.length).toBe(0)
  })

  it('caps the stack at 3 and evicts the oldest', () => {
    const toasts = useToastsStore()
    toasts.info('a')
    toasts.info('b')
    toasts.info('c')
    toasts.info('d')
    expect(toasts.items.length).toBe(3)
    expect(toasts.items.map((t) => t.title)).toEqual(['b', 'c', 'd'])
  })

  it('auto-dismisses after the default 5s TTL', () => {
    const toasts = useToastsStore()
    toasts.error('boom')
    expect(toasts.items.length).toBe(1)
    vi.advanceTimersByTime(5_000)
    expect(toasts.items.length).toBe(0)
  })

  it('records the right kind for each helper', () => {
    const toasts = useToastsStore()
    toasts.success('s')
    toasts.error('e')
    toasts.info('i')
    expect(toasts.items.map((t) => t.kind)).toEqual(['success', 'error', 'info'])
  })
})
