// Shared toast queue. Optional stable keys replace a notice in place, and
// optional actions stay with the caller. Attention notices may pass a zero TTL.
//
// Constraints (deliberate, terse on purpose):
// - Auto-dismiss after 5 s.
// - At most 3 stacked: oldest is evicted when a 4th arrives.
// - Caller can pass `kind: 'error' | 'success' | 'warning' | 'info'` and an optional
//   `detail` string for a second line.
// - Toasts are addressable via the returned `id` so a caller can dismiss
//   programmatically (e.g. once a long-running save finishes).

import { ref } from 'vue'
import { defineStore } from 'pinia'

export type ToastKind = 'error' | 'success' | 'info' | 'warning'

interface ToastOptions {
  key?: string
  actionLabel?: string
  onAction?: () => void
}

export interface Toast extends ToastOptions {
  id: number
  kind: ToastKind
  title: string
  detail?: string
  createdAt: number
}

const DEFAULT_TTL_MS = 5_000
const MAX_STACK = 3

export const useToastsStore = defineStore('toasts', () => {
  const items = ref<Toast[]>([])
  const timers = new Map<number, ReturnType<typeof setTimeout>>()
  let nextId = 1

  function add(kind: ToastKind, title: string, detail?: string, ttlMs = DEFAULT_TTL_MS, options: ToastOptions = {}): number {
    const existing = options.key ? items.value.find((t) => t.key === options.key) : undefined
    const id = existing?.id ?? nextId++
    const toast: Toast = { id, kind, title, detail, createdAt: Date.now(), ...options }
    if (existing) {
      const timer = timers.get(id)
      if (timer !== undefined) clearTimeout(timer)
      timers.delete(id)
      items.value.splice(items.value.indexOf(existing), 1, toast)
    } else items.value.push(toast)
    while (items.value.length > MAX_STACK) {
      const evicted = items.value.shift()
      if (evicted) {
        const t = timers.get(evicted.id)
        if (t !== undefined) clearTimeout(t)
        timers.delete(evicted.id)
      }
    }
    if (ttlMs > 0) {
      const handle = setTimeout(() => dismiss(id), ttlMs)
      timers.set(id, handle)
    }
    return id
  }

  function error(title: string, detail?: string): number {
    return add('error', title, detail)
  }
  function success(title: string, detail?: string): number {
    return add('success', title, detail)
  }
  function info(title: string, detail?: string): number {
    return add('info', title, detail)
  }

  function dismiss(id: number): void {
    const idx = items.value.findIndex((t) => t.id === id)
    if (idx >= 0) items.value.splice(idx, 1)
    const handle = timers.get(id)
    if (handle !== undefined) {
      clearTimeout(handle)
      timers.delete(id)
    }
  }

  function clear(): void {
    for (const handle of timers.values()) clearTimeout(handle)
    timers.clear()
    items.value = []
  }

  return { items, add, error, success, info, dismiss, clear }
})
