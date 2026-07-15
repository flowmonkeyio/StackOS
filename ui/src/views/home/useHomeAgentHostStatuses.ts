import { computed, ref } from 'vue'

import { desktop } from '@/lib/desktop'
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
  const hostStatuses = ref<HostStatusState>({ kind: isShell ? 'loading' : 'idle', items: [] })

  const hostStatusSummary = computed(() => {
    const items = hostStatuses.value.items
    if (hostStatuses.value.kind === 'loading') {
      return items.length ? 'Refreshing connections…' : 'Checking connections…'
    }
    if (hostStatuses.value.kind === 'error') return 'Connection status unavailable'
    return agentHostSummary(items)
  })

  async function loadHostStatuses(): Promise<void> {
    if (!isShell) return
    hostStatuses.value = { kind: 'loading', items: hostStatuses.value.items }
    const result = await desktop.hostStatuses()
    applyHostStatuses(result)
  }

  function applyHostStatuses(
    result: DesktopDoctorResult | DesktopHostStatusesResult | null,
  ): void {
    if (!isShell) return
    const hosts = 'items' in (result ?? {})
      ? (result as DesktopHostStatusesResult).items
      : (result as DesktopDoctorResult | null)?.parsed?.info?.mcp_hosts
    if (Array.isArray(hosts)) {
      hostStatuses.value = { kind: 'loaded', items: hosts }
      return
    }
    hostStatuses.value = {
      kind: 'error',
      items: [],
      message: 'Host tool status is not available from doctor.',
    }
  }

  return {
    hostStatuses,
    hostStatusSummary,
    loadHostStatuses,
    applyHostStatuses,
  }
}
