import { computed, ref } from 'vue'

import { desktop } from '@/lib/desktop'
import type { DesktopDoctorResult, DesktopMcpHostStatus } from '@/lib/desktop'

export type HostStatusState =
  | { kind: 'idle'; items: DesktopMcpHostStatus[] }
  | { kind: 'loading'; items: DesktopMcpHostStatus[] }
  | { kind: 'loaded'; items: DesktopMcpHostStatus[] }
  | { kind: 'error'; items: DesktopMcpHostStatus[]; message: string }

export function useHomeAgentHostStatuses(isShell: boolean) {
  const hostStatuses = ref<HostStatusState>({ kind: isShell ? 'loading' : 'idle', items: [] })

  const hostStatusSummary = computed(() => {
    const items = hostStatuses.value.items
    if (hostStatuses.value.kind === 'loading') return 'Checking host tools'
    if (hostStatuses.value.kind === 'error') return 'Status unavailable'
    if (!items.length) return 'No host status yet'
    const available = items.filter((item) => item.available)
    const absent = items.length - available.length
    if (!available.length) return absent ? `${absent} not installed` : 'No host status yet'
    const connected = available.filter(
      (item) =>
        item.ok &&
        !item.needs_restart &&
        item.status !== 'unsupported_host_version' &&
        item.status !== 'available_unregistered',
    ).length
    const suffix = absent ? ` · ${absent} not installed` : ''
    return `${connected}/${available.length} connected${suffix}`
  })

  async function loadHostStatuses(): Promise<void> {
    if (!isShell) return
    hostStatuses.value = { kind: 'loading', items: hostStatuses.value.items }
    const result = await desktop.runDoctor()
    applyHostStatuses(result)
  }

  function applyHostStatuses(result: DesktopDoctorResult | null): void {
    if (!isShell) return
    const hosts = result?.parsed?.info?.mcp_hosts
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
