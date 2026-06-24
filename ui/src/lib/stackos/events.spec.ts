import { describe, expect, it } from 'vitest'

import { eventActor, humanizeEvent, resolveEventVisual } from './events'
import type { SchemaProjectEventOut } from '@/api'

function event(partial: Partial<SchemaProjectEventOut>): SchemaProjectEventOut {
  return {
    id: 1,
    project_id: 1,
    run_id: null,
    source_type: 'system',
    source_id: null,
    event_type: 'activity',
    title: null,
    summary: null,
    tags: [],
    metadata_json: null,
    occurred_at: '2026-06-23T10:00:00Z',
    created_at: '2026-06-23T10:00:00Z',
    ...partial,
  } as SchemaProjectEventOut
}

describe('resolveEventVisual', () => {
  it('uses the new status tone for tracker task transitions', () => {
    const complete = resolveEventVisual(
      event({ event_type: 'tracker.task.status_changed', metadata_json: { new_status: 'complete' } }),
    )
    expect(complete.tone).toBe('success')
    expect(complete.icon).toBe('check-circle')
    expect(complete.label).toBe('Task update')

    const failed = resolveEventVisual(
      event({ event_type: 'tracker.task.status_changed', metadata_json: { new_status: 'failed' } }),
    )
    expect(failed.tone).toBe('danger')
  })

  it('maps memory event families', () => {
    expect(resolveEventVisual(event({ event_type: 'learning.create' })).label).toBe('Learning')
    expect(resolveEventVisual(event({ event_type: 'decision.record' })).label).toBe('Decision')
    expect(resolveEventVisual(event({ event_type: 'experiment.create' })).icon).toBe('flask-conical')
  })

  it('falls back to a neutral default for unknown dynamic types', () => {
    const visual = resolveEventVisual(event({ event_type: 'some.unknown.kind', source_type: 'x' }))
    expect(visual.tone).toBe('neutral')
    expect(visual.icon).toBe('info')
  })
})

describe('humanizeEvent', () => {
  it('prefers the server-provided title and summary', () => {
    const human = humanizeEvent(event({ title: 'Release shipped', summary: 'v1.2.0' }))
    expect(human).toEqual({ title: 'Release shipped', summary: 'v1.2.0' })
  })

  it('composes a sentence for tracker transitions without a title', () => {
    const human = humanizeEvent(
      event({
        event_type: 'tracker.task.status_changed',
        metadata_json: { new_status: 'complete', task_title: 'Ship docs' },
      }),
    )
    expect(human.title).toBe('Task complete: Ship docs')
  })

  it('never leaks a raw dotted event_type', () => {
    const human = humanizeEvent(event({ event_type: 'weird.internal.thing' }))
    expect(human.title).toBe('Weird · Internal · Thing')
  })
})

describe('eventActor', () => {
  it('reads the actor from metadata', () => {
    expect(eventActor(event({ metadata_json: { actor: 'agent:codex' } }))).toBe('agent:codex')
    expect(eventActor(event({}))).toBeNull()
  })
})
