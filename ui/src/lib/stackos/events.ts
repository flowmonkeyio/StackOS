/**
 * Event taxonomy for the durable project timeline (`ProjectEventOut`).
 *
 * The timeline is the human-readable story of a project: what changed, when,
 * and because of what. Backend `event_type` strings are implementation terms
 * (`tracker.task.status_changed`, `learning.create`, …); this module is the
 * single place that maps them to a calm visual + plain-language label, the same
 * way `design/status.ts` maps status strings. Unknown/dynamic event types
 * (ingress update types, caller-supplied context writes) degrade to a neutral
 * default instead of leaking raw identifiers.
 */

import type { SchemaProjectEventOut } from '@/api'
import type { Tone } from '@/design/status'

export interface EventVisual {
  /** Registry icon name (components/ui/icons.ts). */
  icon: string
  tone: Tone
  /** Short human category, e.g. "Task update", "Decision". */
  label: string
}

export interface HumanEvent {
  title: string
  summary: string | null
}

type EventMeta = Record<string, unknown>

function meta(event: Pick<SchemaProjectEventOut, 'metadata_json'>): EventMeta {
  const value = event.metadata_json
  return value && typeof value === 'object' ? (value as EventMeta) : {}
}

function metaString(m: EventMeta, key: string): string | null {
  const value = m[key]
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

/** "in-progress" / "not_started" → "In progress" / "Not started". */
function humanizeToken(token: string): string {
  const cleaned = token.replace(/[_-]+/g, ' ').trim()
  if (!cleaned) return ''
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1).toLowerCase()
}

/** Tone for a tracker/run status transition carried in event metadata. */
function statusTone(status: string | null): Tone {
  switch (status) {
    case 'complete':
    case 'completed':
    case 'success':
    case 'succeeded':
      return 'success'
    case 'failed':
    case 'aborted':
      return 'danger'
    case 'in-progress':
    case 'running':
    case 'started':
      return 'info'
    default:
      return 'neutral'
  }
}

function statusIcon(status: string | null): string {
  switch (status) {
    case 'complete':
    case 'completed':
    case 'success':
    case 'succeeded':
      return 'check-circle'
    case 'failed':
    case 'aborted':
      return 'x-circle'
    case 'in-progress':
    case 'running':
    case 'started':
      return 'loader'
    default:
      return 'tasks'
  }
}

/**
 * Resolve the icon/tone/label for a timeline event. Order: tracker transitions
 * (tone reflects the new status) → known memory events → source-type fallback →
 * neutral default.
 */
export function resolveEventVisual(
  event: Pick<SchemaProjectEventOut, 'event_type' | 'source_type' | 'metadata_json'>,
): EventVisual {
  const type = event.event_type ?? ''
  const source = event.source_type ?? ''

  if (type === 'tracker.task.status_changed' || type === 'tracker.ticket.status_changed') {
    const status = metaString(meta(event), 'new_status')
    const noun = type === 'tracker.ticket.status_changed' ? 'Ticket' : 'Task'
    return { icon: statusIcon(status), tone: statusTone(status), label: `${noun} update` }
  }
  if (type.startsWith('tracker.')) {
    return { icon: 'tasks', tone: 'neutral', label: 'Work update' }
  }
  if (type.startsWith('learning.')) {
    return { icon: 'light-bulb', tone: 'info', label: 'Learning' }
  }
  if (type.startsWith('decision.')) {
    return { icon: 'shield-check', tone: 'info', label: 'Decision' }
  }
  if (type.startsWith('experiment.')) {
    return { icon: 'flask-conical', tone: 'info', label: 'Experiment' }
  }
  if (type === 'context.snapshot' || type.startsWith('context.')) {
    return { icon: 'database', tone: 'neutral', label: 'Context' }
  }
  if (type.startsWith('run.') || source === 'run' || source === 'run_plan') {
    return { icon: 'runs', tone: 'neutral', label: 'Run' }
  }
  // Dynamic ingress events (Slack/Telegram update types) and anything else.
  if (source === 'communication' || source === 'ingress' || /telegram|slack|message/i.test(type)) {
    return { icon: 'chat', tone: 'info', label: 'Message' }
  }
  return { icon: 'info', tone: 'neutral', label: 'Activity' }
}

/**
 * Plain-language title + summary for a timeline event. Prefers the server's
 * redacted `title`/`summary`; otherwise composes a sentence from known metadata
 * so the feed never shows a raw `event_type`.
 */
export function humanizeEvent(event: SchemaProjectEventOut): HumanEvent {
  const m = meta(event)
  const summary = event.summary?.trim() || null

  if (event.title?.trim()) {
    return { title: event.title.trim(), summary }
  }

  const type = event.event_type ?? ''
  if (type === 'tracker.task.status_changed' || type === 'tracker.ticket.status_changed') {
    const status = metaString(m, 'new_status')
    const noun = type === 'tracker.ticket.status_changed' ? 'Ticket' : 'Task'
    const name = metaString(m, 'task_title') ?? metaString(m, 'task_key') ?? metaString(m, 'ticket_key')
    const verb = status ? humanizeToken(status).toLowerCase() : 'updated'
    return {
      title: name ? `${noun} ${verb}: ${name}` : `${noun} ${verb}`,
      summary,
    }
  }
  if (type.startsWith('learning.')) return { title: 'Agent recorded a learning', summary }
  if (type.startsWith('decision.')) return { title: 'Agent recorded a decision', summary }
  if (type.startsWith('experiment.')) return { title: 'Experiment activity', summary }
  if (type === 'context.snapshot') return { title: 'Context snapshot captured', summary }

  // Last resort: humanize the dotted event type rather than show it raw.
  const label = type
    .split('.')
    .map((part) => humanizeToken(part))
    .filter(Boolean)
    .join(' · ')
  return { title: label || 'Project activity', summary }
}

/** Actor that produced the event, if recorded (metadata `actor`). */
export function eventActor(event: SchemaProjectEventOut): string | null {
  return metaString(meta(event), 'actor')
}
