import { apiStream } from '@/lib/client'
import type { UsageProjection } from '@/lib/usageUpdates'

export interface ProjectTimelineEvent {
  id: number
  project_id: number
  run_id: number | null
  source_type: string
  source_id: number | null
  event_type: string
  title: string | null
  summary: string | null
  tags: string[]
  metadata_json: Record<string, unknown> | string | null
  occurred_at: string
  created_at: string
}

// Batch membership uses the producer's structured members, just like scoped
// timeline readers. Summary keys and nested lookalikes are not membership.
export function trackerEventTaskKeys(event: ProjectTimelineEvent): string[] {
  const metadata = event.metadata_json
  if (!metadata || typeof metadata !== 'object') return []
  if (event.event_type === 'tracker.tasks.changed') {
    if (!Array.isArray(metadata.affected_tasks)) return []
    return [...new Set(metadata.affected_tasks.flatMap(member =>
      member && typeof member === 'object' && typeof member.task_key === 'string' ? [member.task_key] : []))]
  }
  return typeof metadata.task_key === 'string' ? [metadata.task_key] : []
}

export function trackerEventRevision(event: ProjectTimelineEvent): number | undefined {
  const metadata = event.metadata_json
  const rev = metadata && typeof metadata === 'object' ? metadata.rev : undefined
  return typeof rev === 'number' && Number.isSafeInteger(rev) && rev > 0 ? rev : undefined
}

export interface SseMessage {
  id: string | null
  event: string
  data: string
}

export interface TrackerStatusStreamOptions {
  projectId: number | null
  taskKey?: string
  afterId?: number | null
  onEvent: (event: ProjectTimelineEvent) => void
  onError?: (error: unknown) => void
  onUsage?: (updates?: UsageProjection) => void
  onFrame?: (receivedAt: number) => void
  onDisconnect?: () => void
}

export interface TrackerStatusStream {
  close: () => void
}

const TRACKER_STATUS_EVENT = 'tracker-status'
const INITIAL_RETRY_MS = 1000
const MAX_RETRY_MS = 15_000

export class SseMessageParser {
  private buffer = ''

  push(chunk: string): SseMessage[] {
    this.buffer = `${this.buffer}${chunk}`.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
    const messages: SseMessage[] = []
    let boundary = this.buffer.indexOf('\n\n')
    while (boundary >= 0) {
      const block = this.buffer.slice(0, boundary)
      this.buffer = this.buffer.slice(boundary + 2)
      const message = parseSseBlock(block)
      if (message) messages.push(message)
      boundary = this.buffer.indexOf('\n\n')
    }
    return messages
  }
}

export function projectTimelineEventFromMessage(message: SseMessage): ProjectTimelineEvent | null {
  if (message.event !== TRACKER_STATUS_EVENT || !message.data) return null
  try {
    const data = JSON.parse(message.data) as ProjectTimelineEvent
    if (typeof data.metadata_json === 'string') {
      data.metadata_json = parseMetadataJson(data.metadata_json)
    }
    return typeof data.id === 'number' && typeof data.event_type === 'string' ? data : null
  } catch {
    return null
  }
}

export function openTrackerStatusStream(options: TrackerStatusStreamOptions): TrackerStatusStream {
  const controller = new AbortController()
  let closed = false
  let lastEventId = options.afterId ?? null
  let retryTimer: number | null = null
  let resumeRetry: (() => void) | null = null

  async function run(): Promise<void> {
    let retryMs = INITIAL_RETRY_MS
    while (!closed) {
      try {
        await readStream(lastEventId)
        retryMs = INITIAL_RETRY_MS
      } catch (error) {
        if (closed || isAbortError(error)) break
        options.onError?.(error)
      }
      if (!closed) {
        options.onDisconnect?.()
        await waitForRetry(retryMs)
        retryMs = Math.min(MAX_RETRY_MS, Math.round(retryMs * 1.7))
      }
    }
  }

  async function readStream(afterId: number | null): Promise<void> {
    const response = await apiStream(trackerStatusStreamPath(options.projectId, { taskKey: options.taskKey, afterId }), {
      signal: controller.signal,
    })
    const reader = response.body?.getReader()
    if (!reader) throw new Error('Tracker status stream did not provide a readable body.')

    const decoder = new TextDecoder()
    const parser = new SseMessageParser()
    try {
      while (!closed) {
        const { done, value } = await reader.read()
        if (done || closed) break
        const chunk = decoder.decode(value, { stream: true })
        for (const message of parser.push(chunk)) {
          options.onFrame?.(Date.now())
          if (message.event === 'tracker-usage') {
            try { options.onUsage?.(JSON.parse(message.data).updates ?? undefined) } catch { /* Ignore malformed optional telemetry. */ }
            continue
          }
          const event = projectTimelineEventFromMessage(message)
          if (!event) continue
          lastEventId = event.id
          options.onEvent(event)
        }
      }
    } finally {
      reader.releaseLock()
    }
  }

  void run()

  return {
    close() {
      closed = true
      if (retryTimer !== null) {
        window.clearTimeout(retryTimer)
        retryTimer = null
      }
      resumeRetry?.()
      resumeRetry = null
      controller.abort()
    },
  }

  function waitForRetry(ms: number): Promise<void> {
    return new Promise((resolve) => {
      resumeRetry = resolve
      retryTimer = window.setTimeout(() => {
        retryTimer = null
        resumeRetry = null
        resolve()
      }, ms)
    })
  }
}

export function trackerStatusStreamPath(
  projectId: number | null,
  query: URLSearchParams | { taskKey?: string; afterId?: number | null },
): string {
  const params =
    query instanceof URLSearchParams
      ? query
      : new URLSearchParams()
  if (!(query instanceof URLSearchParams)) {
    if (query.taskKey) params.set('task_key', query.taskKey)
    params.set('limit', '50')
  }
  if (!(query instanceof URLSearchParams) && query.afterId !== null && query.afterId !== undefined) {
    params.set('after', String(query.afterId))
  }
  return `${projectId === null ? '/api/v1' : `/api/v1/projects/${projectId}`}/context/timeline/stream?${params.toString()}`
}

function parseSseBlock(block: string): SseMessage | null {
  if (!block.trim()) return null
  let event = 'message'
  let id: string | null = null
  const data: string[] = []
  for (const line of block.split('\n')) {
    if (!line || line.startsWith(':')) continue
    const separator = line.indexOf(':')
    const field = separator >= 0 ? line.slice(0, separator) : line
    const rawValue = separator >= 0 ? line.slice(separator + 1) : ''
    const value = rawValue.startsWith(' ') ? rawValue.slice(1) : rawValue
    if (field === 'event') event = value
    if (field === 'id') id = value
    if (field === 'data') data.push(value)
  }
  return { id, event, data: data.join('\n') }
}

function parseMetadataJson(value: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(value) as unknown
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null
  } catch {
    return null
  }
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}
