import { onBeforeUnmount, ref } from 'vue'
import { callOperation } from '@/lib/operations'
import { ApiError, formatApiError } from '@/lib/client'
import { useToastsStore } from '@/stores/toasts'
import { useHomeSubscriptions } from './useHomeSubscriptions'

export const accountHosts = new Set(['codex', 'claude-code', 'grok'])
export interface DesiredAccount { account_ref: string | null; revision: number }
interface Account { account_ref: string; email: string; identity_status: string }
interface Enrollment { enrollment_ref: string; status: string; expires_at: string; account_ref?: string | null }
export interface HostAccounts {
  desired: DesiredAccount
  accounts: Account[]
  pending_enrollment: Enrollment | null
}
interface Inventory { hosts: Record<string, HostAccounts> }
interface Envelope<T> { data: T }

export function useHomeHostAccounts(canAdopt: (host: string) => boolean) {
  const hosts = ref<Inventory['hosts']>({})
  const errors = ref<Record<string, string>>({})
  const busy = ref<Record<string, boolean>>({})
  const switching = ref<Record<string, boolean>>({})
  const nativeCurrent = ref<Record<string, string>>({})
  const enrollment = ref<{ host: string; value: Enrollment } | null>(null)
  const quotas = useHomeSubscriptions()
  const toasts = useToastsStore()
  let timer: ReturnType<typeof setTimeout> | undefined
  let disposed = false
  let loading: Promise<void> | undefined
  const adopted = new Set<string>()
  let selectionGeneration = 0

  async function load() {
    if (loading) return loading
    const entered = selectionGeneration
    loading = callOperation<Inventory>('hostMcp.listAccounts', {}).then(async result => {
      if (entered !== selectionGeneration || disposed) return
      if (!result?.hosts) throw new Error('Invalid account inventory')
      hosts.value = result.hosts
      await Promise.all([...accountHosts].map(async host => {
        const state = hosts.value[host]
        if (!canAdopt(host) || !state || state.desired.account_ref !== null || state.pending_enrollment || adopted.has(host)) return
        adopted.add(host)
        busy.value[host] = true
        try {
          const result = await callOperation<Envelope<Account>>('hostMcp.adoptAccount', { host_key: host })
          const existing = state.accounts.findIndex(account => account.account_ref === result.data.account_ref)
          if (existing < 0) state.accounts.push(result.data)
          else state.accounts[existing] = result.data
          nativeCurrent.value[host] = result.data.account_ref
          errors.value[host] = ''
        } catch (error) {
          if (!needsSignIn(error)) errors.value[host] = formatApiError(error, 'Current account unavailable.')
        } finally { busy.value[host] = false }
      }))
      for (const [host, state] of Object.entries(result.hosts)) {
        if (accountHosts.has(host) && state.pending_enrollment && !enrollment.value) {
          enrollment.value = { host, value: state.pending_enrollment }
          schedule()
        }
      }
    }).catch(() => {
      for (const host of accountHosts) errors.value[host] = 'Accounts unavailable. Try Refresh.'
    }).finally(() => { loading = undefined })
    return loading
  }

  function needsSignIn(error: unknown) {
    const data = error instanceof ApiError ? error.data as { code?: string; identity_status?: string } | undefined : undefined
    return data?.code === 'identity_unverified' && data.identity_status === 'needs_sign_in'
  }

  function currentRef(host: string) {
    const state = hosts.value[host]
    return state?.desired.account_ref ?? nativeCurrent.value[host]
  }

  async function action(host: string, fn: () => Promise<void>) {
    if (busy.value[host]) return
    busy.value[host] = true
    errors.value[host] = ''
    try { await fn() } catch (error) {
      errors.value[host] = formatApiError(error, 'Account action could not complete.')
    } finally { busy.value[host] = false }
  }

  async function select(host: string, account: string) {
    const state = hosts.value[host]
    if (!state || state.desired.account_ref === account) return
    await action(host, async () => {
      switching.value[host] = true
      try {
        const result = await callOperation<Envelope<DesiredAccount>>('hostMcp.selectAccount', {
          host_key: host, account_ref: account, expected_revision: state.desired.revision,
        })
        selectionGeneration++
        const desired = { account_ref: result.data.account_ref, revision: result.data.revision }
        if (hosts.value[host]) hosts.value[host].desired = desired
        // Quota refresh has its own loading/error state and must not hold the switch open.
        void quotas.accountChanged(host, desired)
        const title = host === 'codex' ? 'Codex account switched' : 'Account switched'
        const manualRestart = host === 'codex' ? 'Restart existing Codex processes to use this account.' : ''
        toasts.success(title, host === 'codex'
          ? `${manualRestart} StackOS automated processes will restart automatically.`
          : undefined)
      } finally { switching.value[host] = false }
    })
  }

  async function add(host: string) {
    if (enrollment.value) return
    await action(host, async () => {
      // Preserve the signed-in native identity before starting another login.
      if (!hosts.value[host]?.accounts.length) {
        try { await callOperation('hostMcp.adoptAccount', { host_key: host }) } catch (error) {
          if (!needsSignIn(error)) throw error
        }
        await load()
      }
      const result = await callOperation<Envelope<Enrollment>>('hostMcp.addAccount', {
        host_key: host, enrollment_ref: crypto.randomUUID(),
      })
      enrollment.value = { host, value: result.data }
      schedule()
    })
  }

  function schedule() {
    clearTimeout(timer)
    if (!disposed && enrollment.value?.value.status === 'pending') timer = setTimeout(() => { void complete() }, 2000)
  }
  async function complete() {
    const current = enrollment.value
    if (!current || busy.value[current.host]) return
    await action(current.host, async () => {
      const result = await callOperation<Envelope<Enrollment>>('hostMcp.completeAccountEnrollment', {
        host_key: current.host, enrollment_ref: current.value.enrollment_ref,
      })
      if (disposed || enrollment.value !== current) return
      enrollment.value = { ...current, value: result.data }
      if (result.data.status !== 'pending') await load()
      schedule()
    })
  }
  async function cancel() {
    const current = enrollment.value
    if (!current) return
    clearTimeout(timer)
    if (current.value.status !== 'pending') { enrollment.value = null; return }
    await action(current.host, async () => {
      await callOperation('hostMcp.cancelAccountEnrollment', {
        host_key: current.host, enrollment_ref: current.value.enrollment_ref,
      })
      enrollment.value = null
      await load()
    })
  }
  onBeforeUnmount(() => { disposed = true; clearTimeout(timer) })
  return { hosts, errors, busy, switching, enrollment, load, select, add, complete, cancel, currentRef }
}
