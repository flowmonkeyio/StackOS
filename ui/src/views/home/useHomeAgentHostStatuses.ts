import { computed, ref } from 'vue'

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

export function useHomeAgentHostStatuses(isShell: boolean) {
  const hostStatuses = ref<HostStatusState>({ kind: 'loading', items: [] })

  const hostStatusSummary = computed(() => {
    const items = hostStatuses.value.items
    if (hostStatuses.value.kind === 'loading') {
      return items.length ? 'Refreshing connections…' : 'Checking connections…'
    }
    if (hostStatuses.value.kind === 'error') return 'Connection status unavailable'
    return agentHostSummary(items)
  })

  async function loadHostStatuses(): Promise<void> {
    hostStatuses.value = { kind: 'loading', items: hostStatuses.value.items }
    try {
      const result = isShell
        ? await desktop.hostStatuses()
        : await callOperation<DesktopHostStatusesResult>('hostMcp.status', {})
      applyHostStatuses(result)
    } catch (err) {
      hostStatuses.value = {
        kind: 'error',
        items: hostStatuses.value.items,
        message: formatApiError(err, 'AI tool connection status is unavailable.'),
      }
    }
  }

  function applyHostStatuses(result: DesktopDoctorResult | DesktopHostStatusesResult | null): void {
    const hosts =
      'items' in (result ?? {})
        ? (result as DesktopHostStatusesResult).items
        : (result as DesktopDoctorResult | null)?.parsed?.info?.mcp_hosts
    if (Array.isArray(hosts)) {
      hostStatuses.value = { kind: 'loaded', items: hosts }
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
