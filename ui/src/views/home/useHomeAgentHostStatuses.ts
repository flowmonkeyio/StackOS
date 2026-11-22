import { computed, getCurrentInstance, onBeforeUnmount, ref, type App } from 'vue'

import { desktop } from '@/lib/desktop'
import { callOperation } from '@/lib/operations'
import { formatApiError } from '@/lib/client'
import type {
  DesktopDoctorResult,
  DesktopHostStatusesResult,
  DesktopMcpHostStatus,
} from '@/lib/desktop'

import { agentHostSummary } from './agentHostPresentation'

export type HostStatusState =
  | { kind: 'idle'; items: DesktopMcpHostStatus[] }
  | { kind: 'loading'; items: DesktopMcpHostStatus[] }
  | { kind: 'loaded'; items: DesktopMcpHostStatus[] }
  | { kind: 'error'; items: DesktopMcpHostStatus[]; message: string }

const retained = new WeakMap<App, DesktopMcpHostStatus[]>()

export function useHomeAgentHostStatuses(isShell: boolean) {
  const app = getCurrentInstance()?.appContext.app
  let disposed = false
  let request = 0
  if (app) onBeforeUnmount(() => { disposed = true })
  const hostStatuses = ref<HostStatusState>({ kind: 'loading', items: (app && retained.get(app)) || [] })

  const hostStatusSummary = computed(() => {
    const items = hostStatuses.value.items
    if (hostStatuses.value.kind === 'loading' && !items.length) return 'Checking connections…'
    if (hostStatuses.value.kind === 'error') return 'Connection status unavailable'
    return agentHostSummary(items)
  })

  async function loadHostStatuses(): Promise<void> {
    if (disposed) return
    const current = ++request
    hostStatuses.value = { kind: 'loading', items: hostStatuses.value.items }
    try {
      const result = isShell
        ? await desktop.hostStatuses()
        : await callOperation<DesktopHostStatusesResult>('hostMcp.status', {})
      if (current === request) applyHostStatuses(result)
    } catch (err) {
      if (disposed || current !== request) return
      hostStatuses.value = {
        kind: 'error',
        items: hostStatuses.value.items,
        message: formatApiError(err, 'AI tool connection status is unavailable.'),
      }
    }
  }

  function applyHostStatuses(result: DesktopDoctorResult | DesktopHostStatusesResult | null): void {
    if (disposed) return
    request++ // A newer Doctor/IPC observation also retires pending readers.
    const hosts =
      'items' in (result ?? {})
        ? (result as DesktopHostStatusesResult).items
        : (result as DesktopDoctorResult | null)?.parsed?.info?.mcp_hosts
    if (Array.isArray(hosts)) {
      hostStatuses.value = { kind: 'loaded', items: hosts }
      if (app) retained.set(app, hosts)
      return
    }
    hostStatuses.value = {
      kind: 'error',
      items: hostStatuses.value.items,
      message: 'AI tool connection status is unavailable.',
    }
  }

  return {
    hostStatuses,
    hostStatusSummary,
    loadHostStatuses,
    applyHostStatuses,
  }
}
