/** A mounted reader's pending work only; no response cache or polling owner. */
export function createUsageReadOwner() {
  const pending = new Map<string, { dirty: number; started: boolean; promise: Promise<unknown> }>()
  let retired = false
  function read<T>(key: string, load: (current: () => boolean) => Promise<T>, options: { invalidate?: boolean; ready?: () => Promise<void> } = {}): Promise<T> {
    const existing = pending.get(key)
    if (existing) {
      // Changes observed before the read begins are already covered by it.
      if (options.invalidate && existing.started) existing.dirty += 1
      return existing.promise as Promise<T>
    }
    const entry = { dirty: 0, started: false, promise: null as unknown as Promise<T> }
    pending.set(key, entry)
    entry.promise = (async () => {
      await options.ready?.()
      entry.started = true
      let value!: T
      let version: number
      do {
        version = entry.dirty
        if (retired) return value
        value = await load(() => !retired && version === entry.dirty)
      } while (!retired && version !== entry.dirty)
      return value
    })().finally(() => { if (pending.get(key) === entry) pending.delete(key) })
    return entry.promise
  }
  return { read, stop: () => { retired = true } }
}
