import { computed, ref } from 'vue'

import type { UiMetadataStripItem } from '@/components/ui/UiMetadataStrip.vue'
import { apiFetch } from '@/lib/client'
import { desktop } from '@/lib/desktop'
import type { DesktopDoctorResult } from '@/lib/desktop'
import { formatDurationMinutes } from '@/lib/stackos/time'
import { useToastsStore } from '@/stores/toasts'
import type { SchemaHealthResponse } from '@/api'

export type HealthState =
  | { kind: 'loading' }
  | { kind: 'ok'; data: SchemaHealthResponse }
  | { kind: 'down' }

export type SystemAction = 'restart' | 'doctor' | 'repair'

interface UseHomeSystemStatusOptions {
  onDoctorResult?: (result: DesktopDoctorResult | null) => void
  onRepairComplete?: () => Promise<void> | void
}

export function useHomeSystemStatus(options: UseHomeSystemStatusOptions = {}) {
  const toasts = useToastsStore()
  const health = ref<HealthState>({ kind: 'loading' })
  const systemBusy = ref<SystemAction | null>(null)

  const statusTone = computed<'success' | 'warning' | 'danger' | 'neutral'>(() => {
    if (health.value.kind === 'loading') return 'neutral'
    if (health.value.kind === 'down') return 'danger'
    return health.value.data.db_status === 'ok' ? 'success' : 'warning'
  })

  const statusLabel = computed(() => {
    if (health.value.kind === 'loading') return 'Checking...'
    if (health.value.kind === 'down') return 'Service unreachable'
    return health.value.data.db_status === 'ok' ? 'Running' : 'Storage degraded'
  })

  const systemFacts = computed<UiMetadataStripItem[]>(() => {
    if (health.value.kind !== 'ok') return []
    const d = health.value.data
    const facts: UiMetadataStripItem[] = []
    if (d.version) facts.push({ label: 'Version', value: `v${d.version}` })
    if (typeof d.daemon_uptime_s === 'number') {
      facts.push({ label: 'Uptime', value: formatDurationMinutes(d.daemon_uptime_s / 60) })
    }
    facts.push({ label: 'Storage', value: d.db_status === 'ok' ? 'Healthy' : 'Degraded' })
    facts.push({ label: 'Automation', value: d.scheduler_running ? 'On' : 'Off' })
    return facts
  })

  async function loadHealth(): Promise<void> {
    try {
      const data = await apiFetch<SchemaHealthResponse>('/api/v1/health')
      health.value = { kind: 'ok', data }
    } catch {
      health.value = { kind: 'down' }
    }
  }

  async function runSystemAction(action: SystemAction): Promise<void> {
    systemBusy.value = action
    try {
      if (action === 'restart') {
        const result = await desktop.restartService()
        toastResult(result?.ok ?? false, 'Service restarted', 'Restart failed')
        await loadHealth()
      } else if (action === 'doctor') {
        const result = await desktop.runDoctor()
        toastResult(result?.ok ?? false, 'Doctor passed', 'Doctor found issues - see the StackOS log')
        options.onDoctorResult?.(result)
      } else if (action === 'repair') {
        const result = await desktop.installOrRepair()
        toastResult(result?.ok ?? false, 'Install or repair complete', 'Install or repair failed')
        await loadHealth()
        await options.onRepairComplete?.()
      }
    } finally {
      systemBusy.value = null
    }
  }

  function toastResult(ok: boolean, okMsg: string, failMsg: string): void {
    if (ok) toasts.success(okMsg)
    else toasts.error(failMsg)
  }

  return {
    health,
    systemBusy,
    statusTone,
    statusLabel,
    systemFacts,
    loadHealth,
    runSystemAction,
  }
}
