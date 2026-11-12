import { openTrackerStatusStream, type ProjectTimelineEvent, type TrackerStatusStream } from '@/lib/task-tracker/liveEvents'
import type { TrackerSnapshot, ActiveExecution } from '@/lib/task-tracker/types'
import type { SchemaAgentStatsMetricsOut, SchemaWorkDurationOut } from '@/api'

type UsageItem = { key: string; agent_stats?: SchemaAgentStatsMetricsOut | null; active_execution?: ActiveExecution | null; work_duration?: SchemaWorkDurationOut | null }
export interface UsageProjection { tasks: UsageItem[]; tickets: UsageItem[]; replace?: boolean }

export function patchUsageSnapshot(target: TrackerSnapshot | null, incoming: UsageProjection): void {
  if (!target) return
  for (const key of ['tasks', 'tickets'] as const) {
    const updates = new Map(incoming[key].map((item) => [item.key, item]))
    for (const item of target[key]) {
      const update = updates.get(item.key)
      if (update) { item.agent_stats = update.agent_stats; item.active_execution = update.active_execution; item.work_duration = update.work_duration }
    }
  }
}

export interface UsageFeedState {
  status: 'connecting' | 'live' | 'reconnecting' | 'paused'
  receivedAt: number | null
}
type Listener = { usage: (updates?: UsageProjection) => void; status?: (event: ProjectTimelineEvent) => void; error?: (cause: unknown) => void; resume?: () => void; connection?: (state: UsageFeedState) => void }
type LatestUsage = { version: number; item: UsageItem }
type Channel = {
  listeners: Set<Listener>; stream: TrackerStatusStream | null; projectId: number | null
  latest: { tasks: Map<string, LatestUsage>; tickets: Map<string, LatestUsage> }
  connection: UsageFeedState; snapshotReady: boolean; freshnessTimer: number | null; generation: number
}
const channels = new Map<number | null, Channel>()
let revision = 0
// The server sends a heartbeat every 15 seconds even when tokens do not change.
const FEED_STALE_MS = 45_000

export function usageCheckpoint(): number { return revision }

export function patchUsageAfter(target: TrackerSnapshot | null, projectId: number, checkpoint: number): void {
  patchUsageSnapshot(target, usageAfter(projectId, checkpoint))
}

export function usageAfter(projectId: number, checkpoint: number): UsageProjection {
  const latest = channels.get(projectId)?.latest
  if (!latest) return { tasks: [], tickets: [] }
  return {
    tasks: [...latest.tasks.values()].filter((value) => value.version > checkpoint).map((value) => value.item),
    tickets: [...latest.tickets.values()].filter((value) => value.version > checkpoint).map((value) => value.item),
  }
}

// One transport/refresh owner shared by Work, Home and Overview. It never reads
// native sessions or owns a sampling timer. Hidden/unmounted consumers release it.
function open(channel: Channel): void {
  if (channel.stream || document.hidden) return
  const generation = ++channel.generation
  const current = () => channel.generation === generation && channels.get(channel.projectId) === channel && !document.hidden
  channel.snapshotReady = false
  publishConnection(channel, channel.connection.receivedAt === null && channel.connection.status !== 'reconnecting' ? 'connecting' : 'reconnecting')
  if (!current()) return
  armFreshness(channel)
  channel.stream = openTrackerStatusStream({
    projectId: channel.projectId,
    taskKey: undefined,
    onUsage: (updates) => {
      if (!current()) return
      if (updates) {
        const version = ++revision
        for (const key of ['tasks', 'tickets'] as const) {
          if (updates.replace) channel.latest[key].clear()
          for (const item of updates[key]) channel.latest[key].set(item.key, { version, item })
        }
      }
      channel.listeners.forEach((listener) => listener.usage(updates))
      if (!current()) return
      if (updates?.replace) {
        channel.snapshotReady = true
        publishConnection(channel, 'live', Date.now())
        armFreshness(channel)
      }
    },
    onFrame: (receivedAt) => {
      if (!current()) return
      publishConnection(channel, channel.snapshotReady ? 'live' : channel.connection.status, receivedAt)
      armFreshness(channel)
    },
    onDisconnect: () => {
      if (!current()) return
      channel.snapshotReady = false
      publishConnection(channel, 'reconnecting')
      armFreshness(channel)
    },
    onEvent: (event) => {
      if (!current()) return
      // Its usage frame already refreshed every consumer. Receipt-only writes
      // do not require a second whole-page Work/status reload.
      if (event.event_type !== 'tracker.usage.changed') channel.listeners.forEach((listener) => listener.status?.(event))
    },
    onError: (cause) => {
      if (!current()) return
      channel.snapshotReady = false
      publishConnection(channel, 'reconnecting')
      channel.listeners.forEach((listener) => listener.error?.(cause))
    },
  })
}

function publishConnection(channel: Channel, status: UsageFeedState['status'], receivedAt = channel.connection.receivedAt): void {
  channel.connection = { status, receivedAt }
  channel.listeners.forEach((listener) => listener.connection?.(channel.connection))
}

function close(channel: Channel): void {
  channel.generation += 1
  if (channel.freshnessTimer !== null) window.clearTimeout(channel.freshnessTimer)
  channel.freshnessTimer = null
  channel.snapshotReady = false
  channel.stream?.close()
  channel.stream = null
}

function armFreshness(channel: Channel): void {
  if (channel.freshnessTimer !== null) window.clearTimeout(channel.freshnessTimer)
  channel.freshnessTimer = null
  if (!channel.listeners.size || document.hidden || channels.get(channel.projectId) !== channel) return
  channel.freshnessTimer = window.setTimeout(() => {
    channel.freshnessTimer = null
    if (channels.get(channel.projectId) !== channel || document.hidden) return
    close(channel)
    publishConnection(channel, 'reconnecting')
    open(channel)
  }, FEED_STALE_MS)
}

function visibilityChanged(): void {
  for (const channel of channels.values()) {
    if (document.hidden) { close(channel); publishConnection(channel, 'paused') }
    else { open(channel); channel.listeners.forEach((listener) => listener.resume ? listener.resume() : listener.usage()) }
  }
}

export function subscribeUsageUpdates(projectId: number | null, listener: Listener): () => void {
  if (!channels.size) document.addEventListener('visibilitychange', visibilityChanged)
  let channel = channels.get(projectId)
  if (!channel) {
    channel = {
      projectId, listeners: new Set(), stream: null, latest: { tasks: new Map(), tickets: new Map() },
      connection: { status: document.hidden ? 'paused' : 'connecting', receivedAt: null },
      snapshotReady: false, freshnessTimer: null, generation: 0,
    }
    channels.set(projectId, channel)
  }
  channel.listeners.add(listener)
  listener.connection?.(channel.connection)
  open(channel)
  return () => {
    channel.listeners.delete(listener)
    if (!channel.listeners.size) {
      close(channel)
      channels.delete(projectId)
    }
    if (!channels.size) document.removeEventListener('visibilitychange', visibilityChanged)
  }
}
