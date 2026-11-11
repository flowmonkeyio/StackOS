// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { parseQuery, stringifyQuery } from 'vue-router'
import {
  boardFilterQuery, clearBoardFilters, parseBoardFilters, removeBoardFilter,
  updateBoardDisplay, updateBoardFilter,
} from './boardFilters'

describe('Board URL state', () => {
  it('distinguishes fresh defaults from explicit unrestricted state after serialization/reload', () => {
    const defaults = parseBoardFilters({})
    expect(defaults).toEqual({
      clauses: [{ field: 'status', operator: 'is-not', values: ['aborted'] }],
      completed: 'past-week', hideEmptyColumns: false,
    })
    const cleared = clearBoardFilters(defaults)
    const query = boardFilterQuery(cleared)
    expect(query).toMatchObject({ status: 'all', completed: 'all' })
    expect(parseBoardFilters(parseQuery(stringifyQuery(query)))).toEqual(cleared)
  })

  it('roundtrips the five fields, limited operators, display choices and encoded text', () => {
    const state = parseBoardFilters({
      search: 'text & spaces', status: ['complete', 'in-progress', 'complete'],
      priority: 'p0,p2', priority_op: 'is-not', workflow: 'engineering.delivery',
      workflow_op: 'is-not', assignee: 'Agent / Reviewer', completed: 'past-month', hide_empty: '1',
    })
    expect(state.clauses).toEqual([
      { field: 'text', value: 'text & spaces' },
      { field: 'status', operator: 'is', values: ['in-progress', 'complete'] },
      { field: 'priority', operator: 'is-not', values: ['p0', 'p2'] },
      { field: 'workflow', operator: 'is-not', value: 'engineering.delivery' },
      { field: 'assignee', operator: 'is', value: 'Agent / Reviewer' },
    ])
    expect(parseBoardFilters(parseQuery(stringifyQuery(boardFilterQuery(state))))).toEqual(state)
  })

  it('normalizes invalid values and repeated lists without accepting arbitrary operators', () => {
    expect(parseBoardFilters({
      status: 'invented', priority: ['p1', 'bad', 'p1'], priority_op: 'contains',
      completed: 'current-cycle', hide_empty: 'yes', search: ['one', 'two'],
      ticket_status: 'bad', focus: 'all',
    })).toEqual({
      clauses: [
        { field: 'status', operator: 'is-not', values: ['aborted'] },
        { field: 'priority', operator: 'is', values: ['p1'] },
      ], completed: 'past-week', hideEmptyColumns: false,
    })
  })

  it('represents legacy Home restrictions as individually removable clauses', () => {
    const state = parseBoardFilters({ ticket_status: 'failed', focus: 'blocked' })
    expect(state.clauses).toContainEqual({ field: 'ticket-status', value: 'failed' })
    expect(state.clauses).toContainEqual({ field: 'blocked' })
    const removed = removeBoardFilter(removeBoardFilter(state, 'ticket-status'), 'blocked')
    expect(boardFilterQuery(removed)).toMatchObject({ ticket_status: undefined, focus: undefined })
    expect(parseBoardFilters(parseQuery(stringifyQuery(boardFilterQuery(removed))))).toEqual(removed)
  })

  it('replaces a field instead of creating duplicate owners and preserves unrelated clauses', () => {
    const state = parseBoardFilters({ search: 'first', priority: 'p3', completed: 'none' })
    const next = updateBoardFilter(state, { field: 'text', value: 'second' })
    expect(next.clauses.filter(clause => clause.field === 'text')).toEqual([{ field: 'text', value: 'second' }])
    expect(next.clauses).toContainEqual({ field: 'priority', operator: 'is', values: ['p3'] })
    expect(removeBoardFilter(next, 'status').clauses.some(clause => clause.field === 'status')).toBe(false)
    expect(state.clauses[0]).toEqual({ field: 'text', value: 'first' })
  })

  it('visibly reconciles Complete plus None to All from either command or a deep link', () => {
    const complete = { field: 'status', operator: 'is', values: ['complete'] } as const
    const hidden = updateBoardDisplay(parseBoardFilters({}), { completed: 'none' })
    const next = updateBoardFilter(hidden, { ...complete, values: [...complete.values] })
    expect(next.completed).toBe('all')
    expect(updateBoardDisplay(next, { completed: 'none' }).completed).toBe('all')
    expect(parseBoardFilters({ status: 'complete', completed: 'none' }).completed).toBe('all')
    expect(clearBoardFilters({ ...next, hideEmptyColumns: true }).hideEmptyColumns).toBe(true)
  })
})
