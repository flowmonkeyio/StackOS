import { apiStream } from '@/lib/client'

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

export interface SseMessage {
  id: string | null
  event: string
  data: string
}

export interface TrackerStatusStreamOptions {
  projectId: number
  taskKey: string
  afterId?: number | null
  onEvent: (event: ProjectTimelineEvent) => void
  onError?: (error: unknown) => void
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
        await waitForRetry(retryMs)
        retryMs = Math.min(MAX_RETRY_MS, Math.round(retryMs * 1.7))
      }
    }
  }

  async function readStream(afterId: number | null): Promise<void> {
    const query = new URLSearchParams({
      task_key: options.taskKey,
      limit: '50',
    })
    if (afterId !== null) query.set('after', String(afterId))
    const response = await apiStream(trackerStatusStreamPath(options.projectId, query), {
      signal: controller.signal,
    })
    const reader = response.body?.getReader()
    if (!reader) throw new Error('Tracker status stream did not provide a readable body.')

    const decoder = new TextDecoder()
    const parser = new SseMessageParser()
    while (!closed) {
      const { done, value } = await reader.read()
      if (done) break
      const chunk = decoder.decode(value, { stream: true })
      for (const message of parser.push(chunk)) {
        const event = projectTimelineEventFromMessage(message)
        if (!event) continue
        lastEventId = event.id
        options.onEvent(event)
      }
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
      controller.abort()
    },
  }

  function waitForRetry(ms: number): Promise<void> {
    return new Promise((resolve) => {
      retryTimer = window.setTimeout(() => {
        retryTimer = null
        resolve()
      }, ms)
    })
  }
}

export function trackerStatusStreamPath(
  projectId: number,
  query: URLSearchParams | { taskKey: string; afterId?: number | null },
): string {
  const params =
    query instanceof URLSearchParams
      ? query
      : new URLSearchParams({
          task_key: query.taskKey,
          limit: '50',
        })
  if (!(query instanceof URLSearchParams) && query.afterId !== null && query.afterId !== undefined) {
    params.set('after', String(query.afterId))
  }
  return `/api/v1/projects/${projectId}/context/timeline/stream?${params.toString()}`
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
