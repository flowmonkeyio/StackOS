/**
 * Visibility-aware light polling for live operational surfaces (Home, Inbox,
 * Activity). A calm console should reflect agent work without a manual refresh,
 * but it must not burn cycles in a hidden tab.
 *
 * This refreshes DATA on an interval; it does not tick clocks. Relative
 * timestamps stay computed-at-render per `lib/stackos/time.ts` — each poll that
 * updates `lastRunAt` naturally re-renders "updated Ns ago" without a watcher.
 */

import { onBeforeUnmount, onMounted, ref, type Ref } from 'vue'

export interface UsePollingOptions {
  /** Poll cadence in ms while the tab is visible. Default 20s. */
  intervalMs?: number
  /** Run once immediately on mount. Default true. */
  immediate?: boolean
}

export interface UsePollingHandle {
  /** Timestamp of the last completed run, or null before the first run. */
  lastRunAt: Ref<Date | null>
  /** True while a run is in flight (prevents overlap). */
  running: Ref<boolean>
  /** Trigger a run now (used by manual refresh buttons). */
  refresh: () => Promise<void>
  /** Stop polling (keeps state). */
  pause: () => void
  /** Resume polling and run once. */
  resume: () => void
}

export function usePolling(
  task: () => void | Promise<void>,
  options: UsePollingOptions = {},
): UsePollingHandle {
  const intervalMs = options.intervalMs ?? 20_000
  const immediate = options.immediate ?? true

  const lastRunAt = ref<Date | null>(null)
  const running = ref(false)
  let timer: ReturnType<typeof setInterval> | null = null

  async function refresh(): Promise<void> {
    if (running.value) return
    running.value = true
    try {
      await task()
      lastRunAt.value = new Date()
    } finally {
      running.value = false
    }
  }

  function startTimer(): void {
    if (timer !== null) return
    timer = setInterval(() => {
      void refresh()
    }, intervalMs)
  }

  function stopTimer(): void {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
  }

  function pause(): void {
    stopTimer()
  }

  function resume(): void {
    void refresh()
    startTimer()
  }

  function onVisibilityChange(): void {
    if (typeof document === 'undefined') return
    if (document.hidden) {
      stopTimer()
    } else {
      void refresh()
      startTimer()
    }
  }

  onMounted(() => {
    if (immediate) void refresh()
    startTimer()
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', onVisibilityChange)
    }
  })

  onBeforeUnmount(() => {
    stopTimer()
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  })

  return { lastRunAt, running, refresh, pause, resume }
}
