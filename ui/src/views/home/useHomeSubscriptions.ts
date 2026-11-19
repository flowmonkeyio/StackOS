import { defineStore } from 'pinia'
import { ref } from 'vue'

import { callOperation } from '@/lib/operations'

interface HostSubscription {
  plan: string | null
  meters: { scope: string; remaining_percent: number; resets_at: string | null }[]
}

interface SubscriptionSnapshot {
  checked_at: string
  readings: Record<string, HostSubscription | null>
}

export interface HomeSubscriptions {
  loading: boolean
  failed: boolean
  checked_at: string | null
  readings: SubscriptionSnapshot['readings']
}

export const useHomeSubscriptions = defineStore('home-subscriptions', () => {
  const subscriptions = ref<HomeSubscriptions>({ loading: true, failed: false, checked_at: null, readings: {} })
  let inFlight: Promise<void> | undefined

  function load(force = false): Promise<void> {
    if (inFlight) return inFlight
    const last = subscriptions.value.checked_at
    if (!force && last && Date.now() - Date.parse(last) < 60_000) return Promise.resolve()
    subscriptions.value.loading = true
    inFlight = callOperation<SubscriptionSnapshot>('hostMcp.subscriptions', {})
      .then(({ checked_at, readings }) => {
        if (!readings || !Number.isFinite(Date.parse(checked_at))) {
          throw new Error('Invalid subscription snapshot')
        }
        subscriptions.value = { loading: false, failed: false, checked_at, readings }
      })
      .catch(() => {
        subscriptions.value = { loading: false, failed: true, checked_at: null, readings: {} }
      })
      .finally(() => { inFlight = undefined })
    return inFlight
  }

  return { subscriptions, load }
})
