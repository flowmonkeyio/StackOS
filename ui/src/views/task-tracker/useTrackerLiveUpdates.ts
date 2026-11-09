import { onBeforeUnmount, ref, type Ref } from 'vue'

import { formatApiError } from '@/lib/client'
import {
  type ProjectTimelineEvent,
} from '@/lib/task-tracker/liveEvents'
import { subscribeUsageUpdates } from '@/lib/usageUpdates'
import type { UsageFeedState, UsageProjection } from '@/lib/usageUpdates'

interface UseTrackerLiveUpdatesOptions {
  projectId: Readonly<Ref<number>>
  activeTaskKey: Readonly<Ref<string>>
  reload: () => Promise<void>
  refreshUsage?: (updates?: UsageProjection) => void
}

export function useTrackerLiveUpdates(options: UseTrackerLiveUpdatesOptions) {
  const { projectId, activeTaskKey, reload } = options
  const liveError = ref<string | null>(null)
  const feedState = ref<UsageFeedState>({ status: 'paused', receivedAt: null })
  const recentNodeIds = ref<Set<string>>(new Set())
  const recentNodeTimers = new Map<string, number>()
  let unsubscribe: (() => void) | null = null

  function restart(): void {
    stop()
    if (!validProjectId(projectId.value)) return
    liveError.value = null
    unsubscribe = subscribeUsageUpdates(projectId.value, {
      usage: (updates) => options.refreshUsage?.(updates),
      resume: () => { void reload() },
      status: onEvent,
      connection: (state) => {
        feedState.value = state
        if (state.status === 'live') liveError.value = null
      },
      error(cause) {
        liveError.value = formatApiError(cause, 'Live tracker updates paused')
      },
    })
  }

  function stop(): void {
    unsubscribe?.()
    unsubscribe = null
    feedState.value = { status: 'paused', receivedAt: feedState.value.receivedAt }
  }

  function onEvent(event: ProjectTimelineEvent): void {
    const metadata = eventMetadata(event)
    if (activeTaskKey.value && metadata.task_key !== activeTaskKey.value) return
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

  return { liveError, feedState, recentNodeIds, restart, stop }
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
