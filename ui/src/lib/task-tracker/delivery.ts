import type { TrackerTask } from './types'

export interface TaskDeliveryState {
  status: 'queued' | 'running' | 'stopped' | 'complete'
  ticketKey: string | null
  reason: string | null
  stop_context?: TaskDeliveryStopContext | null
}

const causes = {
  unknown: 'Cause not recorded', no_ready_work: 'No ticket is ready',
  candidate_validation: 'Candidate validation failed', native_failure: 'Native invocation failed',
  output_validation: 'Output validation failed', agent_blocked: 'Required work is blocked',
  agent_failed: 'Agent reported failure', repair_limit: 'Automatic repair limit reached',
  workflow_changed: 'Workflow occurrence changed', execution_requirement: 'Execution requirement unavailable',
}
const facts = {
  explicit_blocker: 'explicit blockers', dependency_wait: 'unresolved dependencies',
  non_pending_work: 'unfinished work is not pending', containment_wait: 'unfinished contained work',
}
const actions = {
  reconcile_prerequisites: 'Reconcile prerequisites', inspect_retained_evidence: 'Inspect retained evidence',
  reconcile_candidate: 'Reconcile the candidate', resolve_blocker: 'Resolve the blocker',
}
const conditions = {
  prerequisites_ready: 'Prerequisites must be ready before an authorized handoff.',
  explicit_rehandoff: 'Resume requires explicit reconciliation and an authorized handoff.',
  valid_candidate: 'Resume requires a validated candidate and an authorized handoff.',
}
export interface TaskDeliveryStopContext {
  code: keyof typeof causes
  phase: 'delivery' | 'review' | null
  facts: (keyof typeof facts)[]
  evidence_refs: string[]
  next_owner: 'main_agent' | 'operator' | null
  next_action: keyof typeof actions | null
  resume_condition: keyof typeof conditions | null
}

function stopContext(value: unknown): TaskDeliveryStopContext | null {
  if (!isRecord(value) || typeof value.code !== 'string' || !Object.hasOwn(causes, value.code)) return null
  const optional = (key: string) => typeof value[key] === 'string' ? value[key] : null
  const phase = optional('phase'), owner = optional('next_owner')
  const action = optional('next_action'), condition = optional('resume_condition')
  const items = (key: string, limit: number) => Array.isArray(value[key]) ? value[key].slice(0, limit).filter((item): item is string => typeof item === 'string') : []
  const why = items('facts', 4), refs = items('evidence_refs', 8)
  if ((phase !== null && !['delivery', 'review'].includes(phase))
    || (owner !== null && !['main_agent', 'operator'].includes(owner))
    || (action !== null && !Object.hasOwn(actions, action))
    || (condition !== null && !Object.hasOwn(conditions, condition))
    || why.some(item => !Object.hasOwn(facts, item))
    || refs.some(item => item.length > 160 || !/^tracker-ticket:[1-9][0-9]*:[1-9][0-9]*(?::attempt:[1-9][0-9]*:(?:delivery|review))?$/.test(item))) return null
  return { code: value.code, phase, facts: why, evidence_refs: refs, next_owner: owner, next_action: action, resume_condition: condition } as TaskDeliveryStopContext
}

export function stopContextReason(context: TaskDeliveryStopContext): string {
  return `${causes[context.code]}${context.facts.length ? `: ${context.facts.map(fact => facts[fact]).join(', ')}` : ''}.`
}
export function stopContextAction(context: TaskDeliveryStopContext): string | null {
  if (!context.next_action) return null
  const owner = context.next_owner === 'main_agent' ? 'Main agent' : context.next_owner === 'operator' ? 'Operator' : null
  return `${owner ? `${owner}: ` : ''}${actions[context.next_action]}.`
}
export function stopContextResume(context: TaskDeliveryStopContext): string | null {
  return context.resume_condition ? conditions[context.resume_condition] : null
}

/** The persisted handoff identifies the delivery method even after execution ends. */
type DeliveryTask = { delivery?: (Omit<TaskDeliveryState, 'stop_context'> & { stop_context?: unknown }) | null; metadata_json?: TrackerTask['metadata_json'] }

export function isAutomaticDeliveryTask(task: DeliveryTask | null | undefined): boolean {
  return taskDeliveryState(task) !== null
}

/** Execution state is separate from the task and workflow lifecycle. */
export function taskDeliveryState(task: DeliveryTask | null | undefined): TaskDeliveryState | null {
  if (task?.delivery !== undefined) {
    if (!task.delivery) return null
    const { stop_context: raw, ...delivery } = task.delivery
    const context = delivery.status === 'stopped' ? stopContext(raw) : null
    return { ...delivery, reason: delivery.status === 'stopped' ? safeReason(delivery.reason) : null, ...(context ? { stop_context: context } : {}) }
  }
  const metadata = task?.metadata_json
  if (!isRecord(metadata)) return null
  const delivery = metadata.delivery
  if (!isRecord(delivery)
    || typeof delivery.status !== 'string'
    || !['queued', 'running', 'stopped', 'complete'].includes(delivery.status)
    || !nonemptyString(delivery.job_ref)
    || !isFrozenAgent(delivery.delivery_agent)
    || !isFrozenAgent(delivery.review_agent)) return null
  const context = delivery.status === 'stopped' ? stopContext(delivery.stop_context) : null
  return {
    status: delivery.status as TaskDeliveryState['status'],
    ticketKey: nonemptyString(delivery.ticket_key) ? delivery.ticket_key.trim() : null,
    reason: delivery.status === 'stopped' ? safeReason(delivery.reason) : null,
    ...(context ? { stop_context: context } : {}),
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function nonemptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

function safeReason(value: unknown): string | null {
  // Keep this retained-metadata fallback aligned with TaskDeliveryStateOut.safe_reason.
  return nonemptyString(value) ? value.trim().replace(
    /https?:\/\/[^\s'"<>]+|(?<![\w:/])(?:file:\/+|[A-Za-z]:[\\/]|\/|\\\\)[^\s'"<>]+/gi,
    path => /^https?:\/\//i.test(path) ? path : '[local path]' + (path.match(/[.,;:!?)\]}]+$/)?.[0] ?? ''),
  ).slice(0, 500) : null
}

function isFrozenAgent(value: unknown): boolean {
  return isRecord(value) && nonemptyString(value.name) && nonemptyString(value.instructions)
}
