import { ref, type ComputedRef } from 'vue'
import type { ViewportTransform } from '@vue-flow/core'

interface UseTrackerGraphViewportOptions {
  storageKey: ComputedRef<string>
  refocusScopeKey: ComputedRef<string>
}

export function useTrackerGraphViewport(options: UseTrackerGraphViewportOptions) {
  const viewport = ref<ViewportTransform | null>(null)
  const refocusKey = ref('')
  let refocusSeq = 0

  function requestRefocus(): void {
    refocusKey.value = `${options.refocusScopeKey.value}:${++refocusSeq}`
  }

  function restore(): boolean {
    if (typeof window === 'undefined') return false
    const raw = window.sessionStorage.getItem(options.storageKey.value)
    if (!raw) {
      viewport.value = null
      return false
    }
    try {
      const parsed = JSON.parse(raw) as Partial<ViewportTransform>
      viewport.value = validViewport(parsed)
      return viewport.value !== null
    } catch {
      viewport.value = null
      return false
    }
  }

  function onReady(appliedRefocusKey: string): void {
    if (appliedRefocusKey && refocusKey.value === appliedRefocusKey) refocusKey.value = ''
  }

  function onChange(nextViewport: ViewportTransform): void {
    viewport.value = nextViewport
    if (typeof window === 'undefined') return
    window.sessionStorage.setItem(options.storageKey.value, JSON.stringify(nextViewport))
  }

  return { viewport, refocusKey, requestRefocus, restore, onReady, onChange }
}

function validViewport(value: Partial<ViewportTransform>): ViewportTransform | null {
  return typeof value.x === 'number' &&
    typeof value.y === 'number' &&
    typeof value.zoom === 'number'
    ? { x: value.x, y: value.y, zoom: value.zoom }
    : null
}
