import { onBeforeUnmount, ref } from 'vue'
import { callOperation } from '@/lib/operations'
import { ApiError, formatApiError } from '@/lib/client'
import { useToastsStore } from '@/stores/toasts'
import { useHomeSubscriptions } from './useHomeSubscriptions'

export const accountHosts = new Set(['codex', 'claude-code', 'grok'])
export interface DesiredAccount { account_ref: string | null; revision: number }
interface Account { account_ref: string; email: string; identity_status: string }
interface Enrollment { enrollment_ref: string; status: string; expires_at: string; account_ref?: string | null; cleanup_pending?: boolean }
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
  const inventoryError = ref('')
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
  const attempted = new Set<string>()
  const availability = new Map<string, boolean>()
  const generations = new Map<string, number>()
  let inventoryGeneration = 0
  let selectionGeneration = 0

  function generation(host: string) { return generations.get(host) ?? 0 }
  function reconcileAvailability() {
    let changed = false
    for (const host of accountHosts) {
      const available = canAdopt(host)
      const previous = availability.get(host)
      availability.set(host, available)
      if (previous === undefined || previous === available) continue
      changed = true
      inventoryGeneration++
      generations.set(host, generation(host) + 1)
      busy.value[host] = false
      switching.value[host] = false
      if (!available) {
        attempted.delete(host)
        adopted.delete(host)
        delete nativeCurrent.value[host]
        errors.value[host] = ''
      }
    }
    if (changed) schedule()
  }

  async function load(retry = false) {
    if (disposed) return
    if (loading) return loading
    if (retry) attempted.clear()
    const entered = selectionGeneration
    const inventoryEntered = inventoryGeneration
    const valid = () => !disposed && entered === selectionGeneration && inventoryEntered === inventoryGeneration
    loading = callOperation<Inventory>('hostMcp.listAccounts', {}).then(async result => {
      if (!valid()) return
      if (!result?.hosts) throw new Error('Invalid account inventory')
      inventoryError.value = ''
      hosts.value = result.hosts
      await Promise.all([...accountHosts].map(async host => {
        const state = hosts.value[host]
        if (!canAdopt(host) || !state || state.desired.account_ref !== null || state.pending_enrollment || adopted.has(host) || attempted.has(host) || busy.value[host]) return
        attempted.add(host)
        const hostEntered = generation(host)
        const current = () => !disposed && hostEntered === generation(host)
        busy.value[host] = true
        try {
          const result = await callOperation<Envelope<Account>>('hostMcp.adoptAccount', { host_key: host })
          if (!current()) return
          adopted.add(host)
          const existing = state.accounts.findIndex(account => account.account_ref === result.data.account_ref)
          if (existing < 0) state.accounts.push(result.data)
          else state.accounts[existing] = result.data
          nativeCurrent.value[host] = result.data.account_ref
          errors.value[host] = ''
        } catch (error) {
          if (current()) errors.value[host] = needsSignIn(error) ? '' : formatApiError(error, 'Current account unavailable.')
        } finally { if (current()) busy.value[host] = false }
      }))
      if (!valid()) return
      for (const [host, state] of Object.entries(result.hosts)) {
        if (accountHosts.has(host) && state.pending_enrollment && !enrollment.value) {
          enrollment.value = { host, value: state.pending_enrollment }
          schedule()
        }
      }
    }).catch(() => {
      if (valid()) inventoryError.value = 'Accounts unavailable. Try Refresh.'
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

  async function action(host: string, fn: (current: () => boolean) => Promise<void>) {
    if (disposed || busy.value[host]) return
    const entered = generation(host)
    const current = () => !disposed && entered === generation(host)
    busy.value[host] = true
    errors.value[host] = ''
    try { await fn(current) } catch (error) {
      if (current()) errors.value[host] = formatApiError(error, 'Account action could not complete.')
    } finally { if (current()) busy.value[host] = false }
  }

  async function select(host: string, account: string) {
    const state = hosts.value[host]
    if (!state || state.desired.account_ref === account) return
    await action(host, async current => {
      switching.value[host] = true
      try {
        const result = await callOperation<Envelope<DesiredAccount>>('hostMcp.selectAccount', {
          host_key: host, account_ref: account, expected_revision: state.desired.revision,
        })
        if (!current()) return
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
      } finally { if (current()) switching.value[host] = false }
    })
  }

  async function add(host: string) {
    if (enrollment.value) return
    if (!canAdopt(host)) return
    await action(host, async current => {
      // Preserve the signed-in native identity before starting another login.
      if (!hosts.value[host]?.accounts.length) {
        try { await callOperation('hostMcp.adoptAccount', { host_key: host }) } catch (error) {
          if (!needsSignIn(error)) throw error
        }
        if (!current()) return
        await load()
      }
      if (!current()) return
      const result = await callOperation<Envelope<Enrollment>>('hostMcp.addAccount', {
        host_key: host, enrollment_ref: crypto.randomUUID(),
      })
      if (!current()) return
      enrollment.value = { host, value: result.data }
      schedule()
    })
  }

  function schedule() {
    clearTimeout(timer)
    const current = enrollment.value
    if (disposed || current?.value.status !== 'pending') return
    const remaining = Date.parse(current.value.expires_at) - Date.now()
    if (remaining <= 0) {
      // Expiry does not prove native cleanup. Keep that evidence until confirmed.
      enrollment.value = { ...current, value: { ...current.value, status: 'expired', cleanup_pending: true } }
      return
    }
    const delay = canAdopt(current.host) ? Math.min(2000, remaining) : remaining
    if (Number.isFinite(delay)) timer = setTimeout(() => {
      if (canAdopt(current.host)) void complete()
      else schedule()
    }, delay)
  }
  async function complete() {
    const current = enrollment.value
    if (!current || current.value.status !== 'pending' || !canAdopt(current.host) || busy.value[current.host]) return
    await action(current.host, async valid => {
      const result = await callOperation<Envelope<Enrollment>>('hostMcp.completeAccountEnrollment', {
        host_key: current.host, enrollment_ref: current.value.enrollment_ref,
      })
      if (!valid() || enrollment.value !== current) return
      enrollment.value = { ...current, value: result.data }
      if (result.data.status !== 'pending') await load()
    })
    schedule()
  }
  async function cancel() {
    const current = enrollment.value
    if (!current) return
    clearTimeout(timer)
    if (current.value.status !== 'pending' && !current.value.cleanup_pending) { enrollment.value = null; return }
    await action(current.host, async valid => {
      const result = await callOperation<Envelope<Enrollment>>('hostMcp.cancelAccountEnrollment', {
        host_key: current.host, enrollment_ref: current.value.enrollment_ref,
      })
      if (!valid() || enrollment.value !== current) return
      inventoryGeneration++
      enrollment.value = result.data.cleanup_pending ? { ...current, value: result.data } : null
      await load()
    })
  }
  onBeforeUnmount(() => { disposed = true; clearTimeout(timer) })
  return { hosts, errors, inventoryError, busy, switching, enrollment, load, reconcileAvailability, select, add, complete, cancel, currentRef }
}
