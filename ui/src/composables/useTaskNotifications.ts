import { onBeforeUnmount, onMounted } from 'vue'
import { desktop, type DesktopTaskNotice } from '@/lib/desktop'
import { useToastsStore } from '@/stores/toasts'

/** The desktop controller owns event selection and cursors; this only presents it. */
export function useTaskNotifications(): void {
  const toasts = useToastsStore()
  let unsubscribe = () => {}

  function present(notice: DesktopTaskNotice): void {
    const kind = notice.tone === 'danger' ? 'error' : notice.tone
    const nativeHint = notice.nativeStatus === 'unsupported'
      ? 'Native notifications are unavailable on this system.'
      : notice.nativeStatus === 'failed' ? 'Native notification could not be shown. Check system notification settings.' : ''
    toasts.add(kind, notice.title, [notice.body, nativeHint].filter(Boolean).join('\n'),
      notice.tone === 'success' ? 5_000 : 0, {
        key: `task:${notice.projectId}:${notice.taskId}`,
        actionLabel: notice.actionLabel,
        onAction: async () => {
          const result = await desktop.openNotificationTask({
            projectId: notice.projectId, taskId: notice.taskId, taskKey: notice.taskKey,
          })
          if (!result?.ok) toasts.error('Task could not be opened', 'It may have been removed or the local service may be unavailable.')
        },
      })
  }

  onMounted(() => { unsubscribe = desktop.onTaskNotice(present) })
  onBeforeUnmount(() => unsubscribe())
}
