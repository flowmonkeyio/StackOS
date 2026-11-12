import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  boardFiltersFromEntry, boardTicketEntry, clearBoardFilters, defaultBoardFilters,
  normalizeBoardFilters, readBoardFilters, removeBoardFilter, saveBoardFilters,
  updateBoardDisplay, updateBoardFilter,
} from './boardFilters'
import type { BoardFilterState } from './boardFilters'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear() })

describe('Board local preferences', () => {
  it('keeps fresh defaults distinct from saved unrestricted Clear all', () => {
    expect(readBoardFilters(7)).toEqual({
      clauses: [{ field: 'status', operator: 'is-not', values: ['aborted'] }],
      completed: 'past-week', hideEmptyColumns: false,
    })
    const cleared = clearBoardFilters({ ...defaultBoardFilters(), hideEmptyColumns: true })
    saveBoardFilters(7, cleared)
    expect(readBoardFilters(7)).toEqual({ clauses: [], completed: 'all', hideEmptyColumns: true })
    expect(readBoardFilters(8)).toEqual(defaultBoardFilters())
    expect(localStorage.length).toBe(1)
  })

  it('roundtrips every clause, operator and display choice in one project record', () => {
    const state: BoardFilterState = {
      clauses: [
        { field: 'text', value: ' text & spaces ' },
        { field: 'status', operator: 'is', values: ['in-progress', 'complete'] },
        { field: 'priority', operator: 'is-not', values: ['p0', 'p2'] },
        { field: 'workflow', operator: 'is-not', value: 'engineering.delivery' },
        { field: 'assignee', operator: 'is', value: 'Agent / Reviewer' },
        { field: 'ticket-status', value: 'failed' }, { field: 'blocked' },
      ], completed: 'past-month', hideEmptyColumns: true,
    }
    saveBoardFilters(7, state)
    expect(readBoardFilters(7)).toEqual(state)
    expect(JSON.parse(localStorage.getItem('stackos:work-board-filters:7')!)).toEqual(state)
  })

  it.each(['{', 'null', '[]', '42', '{}', '{"clauses":null}', JSON.stringify({ clauses: [], completed: 'invented', hideEmptyColumns: false }),
    JSON.stringify({ clauses: [{ field: 'status', values: ['bogus'] }], completed: 'all', hideEmptyColumns: false }),
    JSON.stringify({ clauses: [{ field: 'status', values: ['complete'] }], completed: 'all', hideEmptyColumns: false }),
    JSON.stringify({ clauses: [null], completed: 'all', hideEmptyColumns: false }),
    JSON.stringify({ clauses: [{ field: 'priority', operator: 'contains', values: ['p1'] }], completed: 'all', hideEmptyColumns: false }),
  ])('uses defaults for invalid storage %s', stored => {
    localStorage.setItem('stackos:work-board-filters:7', stored)
    expect(readBoardFilters(7)).toEqual(defaultBoardFilters())
  })

  it('guards denied storage access and failed writes without breaking commands', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
    expect(readBoardFilters(7)).toEqual(defaultBoardFilters())
    expect(() => saveBoardFilters(7, clearBoardFilters(defaultBoardFilters()))).not.toThrow()
    vi.stubGlobal('window', { get localStorage() { throw new Error('disabled') } })
    expect(readBoardFilters(7)).toEqual(defaultBoardFilters())
    expect(() => saveBoardFilters(7, defaultBoardFilters())).not.toThrow()
  })

  it('validates one-shot Home intent and exposes its removable clause through the same model', () => {
    const state = boardFiltersFromEntry(boardTicketEntry('failed'))!
    expect(state.clauses).toContainEqual({ field: 'ticket-status', value: 'failed' })
    expect(removeBoardFilter(state, 'ticket-status')).toEqual(defaultBoardFilters())
    expect(boardFiltersFromEntry('tickets:invented')).toBeNull()
    expect(boardFiltersFromEntry(['tickets:failed'])).toBeNull()
  })

  it('replaces fields, removes blank text and retains unrelated preferences', () => {
    const state = updateBoardFilter(defaultBoardFilters(), { field: 'text', value: 'first' })
    const next = updateBoardFilter(state, { field: 'text', value: 'second' })
    expect(next.clauses.filter(clause => clause.field === 'text')).toEqual([{ field: 'text', value: 'second' }])
    expect(updateBoardFilter(next, { field: 'text', value: ' ' })).toEqual(defaultBoardFilters())
    expect(state.clauses[0]).toEqual({ field: 'text', value: 'first' })
  })

  it('reconciles Complete plus None on controls and storage readback', () => {
    const hidden = updateBoardDisplay(defaultBoardFilters(), { completed: 'none' })
    const next = updateBoardFilter(hidden, { field: 'status', operator: 'is', values: ['complete'] })
    expect(next.completed).toBe('all')
    expect(updateBoardDisplay(next, { completed: 'none' }).completed).toBe('all')
    expect(normalizeBoardFilters({ ...next, completed: 'none' }).completed).toBe('all')
  })
})
