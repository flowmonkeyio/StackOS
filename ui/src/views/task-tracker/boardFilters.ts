import type { LocationQuery, LocationQueryRaw } from 'vue-router'
import { trackerStatus } from '@/design/status'
import type { TrackerStatus } from '@/lib/task-tracker/types'

export type FilterOperator = 'is' | 'is-not'
export type TaskPriority = 'p0' | 'p1' | 'p2' | 'p3'
export type BoardFilterClause =
  | { field: 'text'; value: string }
  | { field: 'status'; operator: FilterOperator; values: TrackerStatus[] }
  | { field: 'priority'; operator: FilterOperator; values: TaskPriority[] }
  | { field: 'workflow'; operator: FilterOperator; value: string }
  | { field: 'assignee'; operator: FilterOperator; value: string }
  | { field: 'ticket-status'; value: TrackerStatus }
  | { field: 'blocked' }
export type BoardFilterField = BoardFilterClause['field']
export type CompletedWindow = 'all' | 'past-day' | 'past-week' | 'past-month' | 'none'
export interface BoardFilterState {
  clauses: BoardFilterClause[]
  completed: CompletedWindow
  hideEmptyColumns: boolean
}

export const completedOptions: { value: CompletedWindow; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'past-day', label: 'Past day' },
  { value: 'past-week', label: 'Past week' },
  { value: 'past-month', label: 'Past month' },
  { value: 'none', label: 'None' },
]
const statuses = Object.keys(trackerStatus) as TrackerStatus[]
const priorities: TaskPriority[] = ['p0', 'p1', 'p2', 'p3']
const clauseFields: BoardFilterField[] = ['text', 'status', 'priority', 'workflow', 'assignee', 'ticket-status', 'blocked']
const queryKeys = ['search', 'status', 'status_op', 'priority', 'priority_op', 'workflow', 'workflow_op', 'assignee', 'assignee_op', 'ticket_status', 'focus', 'completed', 'hide_empty']
const text = (value: unknown): string => typeof value === 'string' ? value.trim() : ''
const operator = (value: unknown): FilterOperator => value === 'is-not' ? 'is-not' : 'is'

function list<T extends string>(value: LocationQuery[string], allowed: T[]): T[] {
  const entries = (Array.isArray(value) ? value : [value]).flatMap(item => typeof item === 'string' ? item.split(',') : [])
  return allowed.filter(item => entries.includes(item))
}

/** The route is the only owner. Missing keys use defaults; explicit `all` stays unrestricted. */
export function parseBoardFilters(query: LocationQuery): BoardFilterState {
  const clauses: BoardFilterClause[] = []
  if (text(query.search)) clauses.push({ field: 'text', value: text(query.search) })
  const selectedStatuses = list(query.status, statuses)
  if (selectedStatuses.length) clauses.push({ field: 'status', operator: operator(query.status_op), values: selectedStatuses })
  else if (query.status !== 'all') clauses.push({ field: 'status', operator: 'is-not', values: ['aborted'] })
  const selectedPriorities = list(query.priority, priorities)
  if (selectedPriorities.length) clauses.push({ field: 'priority', operator: operator(query.priority_op), values: selectedPriorities })
  for (const field of ['workflow', 'assignee'] as const) {
    if (text(query[field])) clauses.push({ field, operator: operator(query[`${field}_op`]), value: text(query[field]) })
  }
  const ticketStatus = list(query.ticket_status, statuses)[0]
  if (ticketStatus) clauses.push({ field: 'ticket-status', value: ticketStatus })
  if (query.focus === 'blocked') clauses.push({ field: 'blocked' })
  const completed = completedOptions.find(option => option.value === query.completed)?.value ?? 'past-week'
  return reconcileBoardFilters({ clauses, completed, hideEmptyColumns: query.hide_empty === '1' })
}

function reconcileBoardFilters(state: BoardFilterState): BoardFilterState {
  const completeSelected = state.clauses.some(clause => clause.field === 'status' && clause.operator === 'is' && clause.values.includes('complete'))
  return {
    ...state,
    clauses: clauseFields.flatMap(field => state.clauses.filter(clause => clause.field === field)),
    completed: completeSelected && state.completed === 'none' ? 'all' : state.completed,
  }
}

/** Managed keys include removals so merging this into the route cannot retain a hidden clause. */
export function boardFilterQuery(state: BoardFilterState): LocationQueryRaw {
  const next = reconcileBoardFilters(state)
  const query: LocationQueryRaw = Object.fromEntries(queryKeys.map(key => [key, undefined]))
  query.status = 'all'
  query.completed = next.completed
  query.hide_empty = next.hideEmptyColumns ? '1' : undefined
  for (const clause of next.clauses) {
    if (clause.field === 'text') query.search = clause.value
    else if (clause.field === 'blocked') query.focus = 'blocked'
    else if (clause.field === 'ticket-status') query.ticket_status = clause.value
    else {
      query[clause.field] = 'values' in clause ? clause.values.join(',') : clause.value
      query[`${clause.field}_op`] = clause.operator === 'is-not' ? 'is-not' : undefined
    }
  }
  return query
}

export function removeBoardFilter(state: BoardFilterState, field: BoardFilterField): BoardFilterState {
  return { ...state, clauses: state.clauses.filter(clause => clause.field !== field) }
}

export function updateBoardFilter(state: BoardFilterState, clause: BoardFilterClause): BoardFilterState {
  const next = removeBoardFilter(state, clause.field)
  if (('value' in clause && !clause.value.trim()) || ('values' in clause && !clause.values.length)) return next
  return reconcileBoardFilters({ ...next, clauses: [...next.clauses, clause] })
}

export function updateBoardDisplay(state: BoardFilterState, changes: Partial<Pick<BoardFilterState, 'completed' | 'hideEmptyColumns'>>): BoardFilterState {
  return reconcileBoardFilters({ ...state, ...changes })
}

export function clearBoardFilters(state: BoardFilterState): BoardFilterState {
  return { ...state, clauses: [], completed: 'all' }
}

export function boardFilterLabel(clause: BoardFilterClause): string {
  switch (clause.field) {
    case 'text': return `Text: ${clause.value}`
    case 'status': return `Status ${clause.operator === 'is-not' ? 'is not' : 'is'}: ${clause.values.map(value => trackerStatus[value].label).join(', ')}`
    case 'priority': return `Priority ${clause.operator === 'is-not' ? 'is not' : 'is'}: ${clause.values.map(value => value.toUpperCase()).join(', ')}`
    case 'workflow': return `Workflow ${clause.operator === 'is-not' ? 'is not' : 'is'}: ${clause.value}`
    case 'assignee': return `Assignee ${clause.operator === 'is-not' ? 'is not' : 'is'}: ${clause.value}`
    case 'ticket-status': return `Contains tickets: ${trackerStatus[clause.value].label}`
    case 'blocked': return 'Has blocked tickets'
  }
}
