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
const text = (value: unknown): string => typeof value === 'string' ? value.trim() : ''

export function defaultBoardFilters(): BoardFilterState {
  return { clauses: [{ field: 'status', operator: 'is-not', values: ['aborted'] }], completed: 'past-week', hideEmptyColumns: false }
}

/** Storage is untrusted. An invalid record leaves the existing defaults intact. */
export function normalizeBoardFilters(value: unknown): BoardFilterState {
  if (!value || typeof value !== 'object') return defaultBoardFilters()
  const state = value as Partial<BoardFilterState>
  if (!Array.isArray(state.clauses) || !completedOptions.some(option => option.value === state.completed)
    || typeof state.hideEmptyColumns !== 'boolean') return defaultBoardFilters()
  const clauses: BoardFilterClause[] = []
  for (const clause of state.clauses) {
    if (!clause || typeof clause !== 'object' || !clauseFields.includes(clause.field)
      || clauses.some(item => item.field === clause.field)) return defaultBoardFilters()
    if (['status', 'priority', 'workflow', 'assignee'].includes(clause.field)
      && (!('operator' in clause) || (clause.operator !== 'is' && clause.operator !== 'is-not'))) return defaultBoardFilters()
    if (clause.field === 'blocked') clauses.push({ field: 'blocked' })
    else if (clause.field === 'ticket-status') {
      if (!statuses.includes(clause.value)) return defaultBoardFilters()
      clauses.push({ field: clause.field, value: clause.value })
    } else if (clause.field === 'status' || clause.field === 'priority') {
      const allowed: readonly string[] = clause.field === 'status' ? statuses : priorities
      if (!Array.isArray(clause.values) || !clause.values.length || clause.values.some(item => !allowed.includes(item))) return defaultBoardFilters()
      clauses.push({ ...clause, values: [...new Set(clause.values)] } as BoardFilterClause)
    } else {
      if (!text(clause.value)) return defaultBoardFilters()
      clauses.push({ ...clause })
    }
  }
  return reconcileBoardFilters({ clauses, completed: state.completed!, hideEmptyColumns: state.hideEmptyColumns })
}

export function readBoardFilters(projectId: number): BoardFilterState {
  try { return normalizeBoardFilters(JSON.parse(window.localStorage.getItem(`stackos:work-board-filters:${projectId}`) ?? 'null')) }
  catch { return defaultBoardFilters() }
}

export function saveBoardFilters(projectId: number, state: BoardFilterState): void {
  try { window.localStorage.setItem(`stackos:work-board-filters:${projectId}`, JSON.stringify(state)) }
  catch { /* Local preferences remain usable when storage is unavailable. */ }
}

/** Home links carry a one-shot navigation intent, never a saved preference query. */
export function boardTicketEntry(status: TrackerStatus): string { return `tickets:${status}` }

export function boardFiltersFromEntry(entry: unknown): BoardFilterState | null {
  const status = statuses.find(value => entry === boardTicketEntry(value))
  return status ? updateBoardFilter(defaultBoardFilters(), { field: 'ticket-status', value: status }) : null
}

function reconcileBoardFilters(state: BoardFilterState): BoardFilterState {
  const completeSelected = state.clauses.some(clause => clause.field === 'status' && clause.operator === 'is' && clause.values.includes('complete'))
  return {
    ...state,
    clauses: clauseFields.flatMap(field => state.clauses.filter(clause => clause.field === field)),
    completed: completeSelected && state.completed === 'none' ? 'all' : state.completed,
  }
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
