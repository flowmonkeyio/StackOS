import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  openTrackerStatusStream,
  projectTimelineEventFromMessage,
  SseMessageParser,
  trackerStatusStreamPath,
} from './liveEvents'

const { mockApiStream } = vi.hoisted(() => ({
  mockApiStream: vi.fn(),
}))

vi.mock('@/lib/client', () => ({
  apiStream: mockApiStream,
}))

beforeEach(() => {
  mockApiStream.mockReset()
})

describe('SseMessageParser', () => {
  it('parses id, event, and data frames split across chunks', () => {
    const parser = new SseMessageParser()

    expect(parser.push('id: 12\nevent: tracker-status\n')).toEqual([])
    const messages = parser.push('data: {"id":12,"event_type":"tracker.ticket.status_changed"}\n\n')

    expect(messages).toEqual([
      {
        id: '12',
        event: 'tracker-status',
        data: '{"id":12,"event_type":"tracker.ticket.status_changed"}',
      },
    ])
  })

  it('ignores comments and joins multiline data fields', () => {
    const parser = new SseMessageParser()

    const messages = parser.push(': hello\r\nevent: tracker-status\r\ndata: {"id":13,\r\ndata: "event_type":"tracker.task.status_changed"}\r\n\r\n')

    expect(messages).toHaveLength(1)
    expect(messages[0].data).toBe('{"id":13,\n"event_type":"tracker.task.status_changed"}')
  })
})

describe('projectTimelineEventFromMessage', () => {
  it('returns sanitized project timeline events for tracker-status messages', () => {
    const event = projectTimelineEventFromMessage({
      id: '22',
      event: 'tracker-status',
      data: JSON.stringify({
        id: 22,
        project_id: 1,
        run_id: null,
        source_type: 'tracker_ticket',
        source_id: 7,
        event_type: 'tracker.ticket.status_changed',
        title: 'Ticket demo is in-progress',
        summary: null,
        tags: ['tracker'],
        metadata_json: { task_key: 'demo', ticket_key: 'demo-ticket' },
        occurred_at: '2026-06-30T00:00:00Z',
        created_at: '2026-06-30T00:00:00Z',
      }),
    })

    expect(
      event?.metadata_json && typeof event.metadata_json === 'object'
        ? event.metadata_json.ticket_key
        : null,
    ).toBe('demo-ticket')
  })

  it('normalizes string metadata payloads from tracker-status messages', () => {
    const event = projectTimelineEventFromMessage({
      id: '23',
      event: 'tracker-status',
      data: JSON.stringify({
        id: 23,
        event_type: 'tracker.ticket.status_changed',
        metadata_json: JSON.stringify({
          task_key: 'demo',
          ticket_key: 'demo-ticket',
        }),
      }),
    })

    expect(event?.metadata_json).toEqual({
      task_key: 'demo',
      ticket_key: 'demo-ticket',
    })
  })

  it('drops heartbeat and malformed messages', () => {
    expect(
      projectTimelineEventFromMessage({ id: null, event: 'heartbeat', data: '{"cursor":1}' }),
    ).toBeNull()
    expect(
      projectTimelineEventFromMessage({ id: null, event: 'tracker-status', data: 'nope' }),
    ).toBeNull()
  })
})

describe('openTrackerStatusStream', () => {
  it('opens a task-scoped stream with an after cursor and ignores heartbeats', async () => {
    const onEvent = vi.fn()
    mockApiStream.mockResolvedValueOnce(
      streamResponse(
        [
          'event: heartbeat\ndata: {"cursor":21}\n\n',
          `id: 22\nevent: tracker-status\ndata: ${JSON.stringify(timelineEvent(22))}\n\n`,
        ].join(''),
      ),
    )

    const stream = openTrackerStatusStream({
      projectId: 7,
      taskKey: 'workflow-142',
      afterId: 21,
      onEvent,
    })
    await flushAsync()
    stream.close()

    expect(mockApiStream).toHaveBeenCalledWith(
      '/api/v1/projects/7/context/timeline/stream?task_key=workflow-142&limit=50&after=21',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
    expect(onEvent).toHaveBeenCalledTimes(1)
    expect(onEvent.mock.calls[0][0].id).toBe(22)
  })

  it('aborts the underlying request when closed', async () => {
    let capturedSignal: AbortSignal | undefined
    mockApiStream.mockImplementationOnce((_path: string, init: RequestInit) => {
      capturedSignal = init.signal instanceof AbortSignal ? init.signal : undefined
      return Promise.resolve(streamResponse(''))
    })

    const stream = openTrackerStatusStream({
      projectId: 7,
      taskKey: 'workflow-142',
      onEvent: vi.fn(),
    })
    await flushAsync()
    stream.close()

    expect((capturedSignal as AbortSignal | undefined)?.aborted).toBe(true)
  })
})

describe('trackerStatusStreamPath', () => {
  it('omits after for a fresh selected task and includes it for same-task reconnects', () => {
    expect(trackerStatusStreamPath(7, { taskKey: 'task-b', afterId: null })).toBe(
      '/api/v1/projects/7/context/timeline/stream?task_key=task-b&limit=50',
    )
    expect(trackerStatusStreamPath(7, { taskKey: 'task-a', afterId: 22 })).toBe(
      '/api/v1/projects/7/context/timeline/stream?task_key=task-a&limit=50&after=22',
    )
  })
})

function streamResponse(body: string): Response {
  const encoder = new TextEncoder()
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(body))
        controller.close()
      },
    }),
    { headers: { 'content-type': 'text/event-stream' } },
  )
}

function timelineEvent(id: number) {
  return {
    id,
    project_id: 7,
    run_id: null,
    source_type: 'tracker_ticket',
    source_id: 44,
    event_type: 'tracker.ticket.status_changed',
    title: 'Ticket tracker-chart-live-client is in-progress',
    summary: null,
    tags: ['tracker'],
    metadata_json: { task_key: 'workflow-142', ticket_key: 'tracker-chart-live-client' },
    occurred_at: '2026-06-30T00:00:00Z',
    created_at: '2026-06-30T00:00:00Z',
  }
}

function flushAsync(): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, 0))
}
