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
  selections?: Record<string, { account_ref: string | null; revision: number }>
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
  let generation = 0
  const identities = new Map<string, { account_ref: string | null; revision: number }>()

  function accountChanged(host: string, identity: { account_ref: string | null; revision: number }) {
    identities.set(host, identity)
    generation++
    inFlight = undefined
    subscriptions.value.readings = { ...subscriptions.value.readings, [host]: null }
    subscriptions.value.checked_at = null
    return load(true)
  }

  function load(force = false): Promise<void> {
    if (inFlight) return inFlight
    const last = subscriptions.value.checked_at
    if (!force && last && Date.now() - Date.parse(last) < 60_000) return Promise.resolve()
    subscriptions.value.loading = true
    const entered = generation
    inFlight = callOperation<SubscriptionSnapshot>('hostMcp.subscriptions', {})
      .then(({ checked_at, readings, selections }) => {
        if (entered !== generation) return
        if (!readings || !Number.isFinite(Date.parse(checked_at))) {
          throw new Error('Invalid subscription snapshot')
        }
        const fenced = { ...readings }
        for (const [host, identity] of identities) {
          const reading = fenced[host]
          const selected = selections?.[host]
          if (reading && (selected?.account_ref !== identity.account_ref || selected?.revision !== identity.revision)) fenced[host] = null
        }
        subscriptions.value = { loading: false, failed: false, checked_at, readings: fenced }
      })
      .catch(() => {
        if (entered !== generation) return
        subscriptions.value = { loading: false, failed: true, checked_at: null, readings: {} }
      })
      .finally(() => { if (entered === generation) inFlight = undefined })
    return inFlight
  }

  return { subscriptions, load, accountChanged }
})
