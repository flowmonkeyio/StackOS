// @vitest-environment node
import { describe, expect, it, vi } from 'vitest'
import { createUsageReadOwner } from './usageReads'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}
describe('mounted usage read admission', () => {
  it('joins initial/manual/baseline work and follows a real in-flight burst once', async () => {
    const owner = createUsageReadOwner(), baseline = deferred<void>(), old = deferred<number>()
    const accepted: number[] = []
    const load = vi.fn(async (current: () => boolean) => {
      const value = load.mock.calls.length === 1 ? await old.promise : 2
      if (current()) accepted.push(value)
      return value
    })
    const initial = owner.read('project:1/filter:a', load, { ready: () => baseline.promise })
    expect(owner.read('project:1/filter:a', load)).toBe(initial)
    owner.read('project:1/filter:a', load, { invalidate: true })
    baseline.resolve()
    await Promise.resolve()
    expect(load).toHaveBeenCalledOnce()
    for (let i = 0; i < 20; i++) owner.read('project:1/filter:a', load, { invalidate: true })
    old.resolve(1)
    expect(await initial).toBe(2)
    expect(load).toHaveBeenCalledTimes(2)
    expect(accepted).toEqual([2])
  })
  it('keeps scopes independent and releases queued work on last-owner teardown', async () => {
    const owner = createUsageReadOwner(), late = deferred<number>()
    const load = vi.fn(() => late.promise)
    const first = owner.read('project:1', load)
    await Promise.resolve()
    expect(await owner.read('project:2', async () => 2)).toBe(2)
    owner.read('project:1', load, { invalidate: true })
    owner.stop()
    late.resolve(1)
    await first
    expect(load).toHaveBeenCalledOnce()
  })
})
