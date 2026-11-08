import { openTrackerStatusStream, type ProjectTimelineEvent, type TrackerStatusStream } from '@/lib/task-tracker/liveEvents'
import type { TrackerSnapshot, ActiveExecution } from '@/lib/task-tracker/types'
import type { SchemaAgentStatsMetricsOut } from '@/api'

type UsageItem = { key: string; agent_stats?: SchemaAgentStatsMetricsOut | null; active_execution?: ActiveExecution | null }
export interface UsageProjection { tasks: UsageItem[]; tickets: UsageItem[]; replace?: boolean }

export function patchUsageSnapshot(target: TrackerSnapshot | null, incoming: UsageProjection): void {
  if (!target) return
  for (const key of ['tasks', 'tickets'] as const) {
    const updates = new Map(incoming[key].map((item) => [item.key, item]))
    for (const item of target[key]) {
      const update = updates.get(item.key)
      if (update) { item.agent_stats = update.agent_stats; item.active_execution = update.active_execution }
    }
  }
}

type Listener = { usage: (updates?: UsageProjection) => void; status?: (event: ProjectTimelineEvent) => void; error?: (cause: unknown) => void; resume?: () => void }
type LatestUsage = { version: number; item: UsageItem }
type Channel = { listeners: Set<Listener>; stream: TrackerStatusStream | null; projectId: number | null; latest: { tasks: Map<string, LatestUsage>; tickets: Map<string, LatestUsage> } }
const channels = new Map<number | null, Channel>()
let revision = 0

export function usageCheckpoint(): number { return revision }

export function patchUsageAfter(target: TrackerSnapshot | null, projectId: number, checkpoint: number): void {
  const latest = channels.get(projectId)?.latest
  if (!latest) return
  patchUsageSnapshot(target, {
    tasks: [...latest.tasks.values()].filter((value) => value.version > checkpoint).map((value) => value.item),
    tickets: [...latest.tickets.values()].filter((value) => value.version > checkpoint).map((value) => value.item),
  })
}

// One transport/refresh owner shared by Work, Home and Overview. It never reads
// native sessions or owns a sampling timer. Hidden/unmounted consumers release it.
function open(channel: Channel): void {
  if (channel.stream || document.hidden) return
  channel.stream = openTrackerStatusStream({
    projectId: channel.projectId,
    taskKey: undefined,
    onUsage: (updates) => {
      if (updates) {
        const version = ++revision
        for (const key of ['tasks', 'tickets'] as const) {
          if (updates.replace) channel.latest[key].clear()
          for (const item of updates[key]) channel.latest[key].set(item.key, { version, item })
        }
      }
      channel.listeners.forEach((listener) => listener.usage(updates))
    },
    onEvent: (event) => channel.listeners.forEach((listener) => listener.status?.(event)),
    onError: (cause) => channel.listeners.forEach((listener) => listener.error?.(cause)),
  })
}

function visibilityChanged(): void {
  for (const channel of channels.values()) {
    if (document.hidden) { channel.stream?.close(); channel.stream = null }
    else { open(channel); channel.listeners.forEach((listener) => listener.resume ? listener.resume() : listener.usage()) }
  }
}

export function subscribeUsageUpdates(projectId: number | null, listener: Listener): () => void {
  if (!channels.size) document.addEventListener('visibilitychange', visibilityChanged)
  let channel = channels.get(projectId)
  if (!channel) {
    channel = { projectId, listeners: new Set(), stream: null, latest: { tasks: new Map(), tickets: new Map() } }
    channels.set(projectId, channel)
  }
  channel.listeners.add(listener)
  open(channel)
  return () => {
    channel.listeners.delete(listener)
    if (!channel.listeners.size) {
      channel.stream?.close()
      channels.delete(projectId)
    }
    if (!channels.size) document.removeEventListener('visibilitychange', visibilityChanged)
  }
}
