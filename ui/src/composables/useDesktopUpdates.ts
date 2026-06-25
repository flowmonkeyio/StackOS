import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { desktop, isDesktopShell } from '@/lib/desktop'
import type { DesktopUpdateResult, DesktopUpdateState } from '@/lib/desktop'

type UpdateAction = 'check' | 'download' | 'install'

export function useDesktopUpdates() {
  const isShell = ref(false)
  const updateState = ref<DesktopUpdateState | null>(null)
  const busy = ref<UpdateAction | null>(null)
  const actionError = ref<string | null>(null)
  let pollTimer: ReturnType<typeof setTimeout> | null = null

  const version = computed(() => updateInfoVersion(updateState.value?.updateInfo))
  const status = computed(() => updateState.value?.status ?? 'idle')
  const percent = computed(() => {
    const raw = updateState.value?.progress?.percent
    return typeof raw === 'number' ? Math.max(0, Math.min(100, Math.round(raw))) : null
  })

  const promptVisible = computed(() => {
    if (!isShell.value || !updateState.value?.enabled) return false
    return (
      status.value === 'available' ||
      status.value === 'downloading' ||
      status.value === 'downloaded' ||
      Boolean(actionError.value)
    )
  })

  const canClick = computed(
    () =>
      status.value === 'available' ||
      status.value === 'downloaded' ||
      Boolean(actionError.value),
  )

  function clearPoll(): void {
    if (pollTimer) clearTimeout(pollTimer)
    pollTimer = null
  }

  function applyResult(result: DesktopUpdateResult | DesktopUpdateState | null): void {
    if (!result) return
    const nextState = hasStateEnvelope(result) ? result.state : result
    if (isUpdateState(nextState)) updateState.value = nextState
  }

  async function refreshState(): Promise<void> {
    applyResult(await desktop.updateState())
    if (status.value === 'downloading') scheduleDownloadPoll()
  }

  async function runCheck(showErrors = false): Promise<void> {
    busy.value = 'check'
    try {
      const result = await desktop.checkForUpdates()
      applyResult(result)
      if (!result?.ok && showErrors) {
        actionError.value = result?.reason || 'StackOS could not read the update feed.'
      }
    } finally {
      busy.value = null
    }
  }

  async function runDownload(): Promise<void> {
    busy.value = 'download'
    actionError.value = null
    try {
      const result = await desktop.downloadUpdate()
      applyResult(result)
      if (!result?.ok) {
        actionError.value = result?.reason || 'StackOS could not download the update.'
        return
      }
      if (status.value === 'downloading') scheduleDownloadPoll()
    } finally {
      busy.value = null
    }
  }

  async function runInstall(): Promise<void> {
    busy.value = 'install'
    actionError.value = null
    try {
      const result = await desktop.installUpdate()
      applyResult(result)
      if (!result?.ok) {
        if (result?.reason === 'download the update before installing') return
        actionError.value = result?.reason || 'StackOS could not install the downloaded update.'
      }
    } finally {
      busy.value = null
    }
  }

  async function runPrimaryAction(): Promise<void> {
    if (busy.value) return
    if (actionError.value) {
      actionError.value = null
      await runCheck(true)
      return
    }
    if (status.value === 'available') {
      await runDownload()
    } else if (status.value === 'downloaded') {
      await runInstall()
    }
  }

  function scheduleDownloadPoll(): void {
    clearPoll()
    pollTimer = setTimeout(async () => {
      await refreshState()
    }, 1500)
  }

  onMounted(async () => {
    isShell.value = isDesktopShell()
    if (!isShell.value) return
    await refreshState()
    if (status.value === 'idle' || status.value === 'not-available' || status.value === 'error') {
      await runCheck(false)
    }
  })

  onBeforeUnmount(clearPoll)

  return {
    isShell,
    updateState,
    busy,
    actionError,
    status,
    version,
    percent,
    promptVisible,
    canClick,
    refreshState,
    runCheck,
    runDownload,
    runInstall,
    runPrimaryAction,
  }
}

function updateInfoVersion(info: unknown): string | null {
  if (!info || typeof info !== 'object') return null
  const version = (info as { version?: unknown }).version
  return typeof version === 'string' && version.trim() ? version : null
}

function hasStateEnvelope(value: DesktopUpdateResult | DesktopUpdateState): value is DesktopUpdateResult {
  return 'state' in value
}

function isUpdateState(value: unknown): value is DesktopUpdateState {
  return Boolean(value && typeof value === 'object' && typeof (value as { status?: unknown }).status === 'string')
}
