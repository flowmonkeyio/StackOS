import { onBeforeUnmount, ref, type Ref } from 'vue'

import { formatApiError } from '@/lib/client'
import {
  openTrackerStatusStream,
  type ProjectTimelineEvent,
  type TrackerStatusStream,
} from '@/lib/task-tracker/liveEvents'

interface UseTrackerLiveUpdatesOptions {
  projectId: Readonly<Ref<number>>
  activeTaskKey: Readonly<Ref<string>>
  reload: () => Promise<void>
}

const streamCursorByProjectTask = new Map<string, number>()

export function useTrackerLiveUpdates(options: UseTrackerLiveUpdatesOptions) {
  const { projectId, activeTaskKey, reload } = options
  const liveError = ref<string | null>(null)
  const recentNodeIds = ref<Set<string>>(new Set())
  const recentNodeTimers = new Map<string, number>()
  let statusStream: TrackerStatusStream | null = null

  function restart(): void {
    stop()
    if (!validProjectId(projectId.value) || !activeTaskKey.value) return
    const taskKey = activeTaskKey.value
    const cursorKey = projectTaskCursorKey(projectId.value, taskKey)
    liveError.value = null
    statusStream = openTrackerStatusStream({
      projectId: projectId.value,
      taskKey,
      afterId: streamCursorByProjectTask.get(cursorKey) ?? null,
      onEvent,
      onError(cause) {
        liveError.value = formatApiError(cause, 'Live tracker updates paused')
      },
    })
  }

  function stop(): void {
    statusStream?.close()
    statusStream = null
  }

  function onEvent(event: ProjectTimelineEvent): void {
    const metadata = eventMetadata(event)
    const taskKey = typeof metadata.task_key === 'string' ? metadata.task_key : activeTaskKey.value
    if (taskKey && validProjectId(projectId.value)) {
      streamCursorByProjectTask.set(projectTaskCursorKey(projectId.value, taskKey), event.id)
    }
    const touchedNodes = touchedGraphNodeIds(event)
    markRecentNodes(touchedNodes)
    void reload().then(() => markRecentNodes(touchedNodes))
  }

  function markRecentNodes(nodeIds: string[]): void {
    if (!nodeIds.length || typeof window === 'undefined') return
    const next = new Set(recentNodeIds.value)
    for (const nodeId of nodeIds) {
      next.add(nodeId)
      const existing = recentNodeTimers.get(nodeId)
      if (existing) window.clearTimeout(existing)
      recentNodeTimers.set(
        nodeId,
        window.setTimeout(() => {
          const reduced = new Set(recentNodeIds.value)
          reduced.delete(nodeId)
          recentNodeIds.value = reduced
          recentNodeTimers.delete(nodeId)
        }, 4500),
      )
    }
    recentNodeIds.value = next
  }

  onBeforeUnmount(() => {
    stop()
    for (const timer of recentNodeTimers.values()) window.clearTimeout(timer)
    recentNodeTimers.clear()
  })

  return { liveError, recentNodeIds, restart, stop }
}

function projectTaskCursorKey(projectId: number, taskKey: string): string {
  return `${projectId}:${taskKey}`
}

function validProjectId(projectId: number): boolean {
  return Boolean(projectId) && !Number.isNaN(projectId)
}

function eventMetadata(event: ProjectTimelineEvent): Record<string, unknown> {
  return event.metadata_json && typeof event.metadata_json === 'object' ? event.metadata_json : {}
}

function touchedGraphNodeIds(event: ProjectTimelineEvent): string[] {
  const metadata = eventMetadata(event)
  if (typeof metadata.ticket_key === 'string') return [`ticket:${metadata.ticket_key}`]
  if (typeof metadata.task_key === 'string') return [`task:${metadata.task_key}`]
  return []
}
