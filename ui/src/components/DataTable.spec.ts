import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import DataTable from './DataTable.vue'
import type { DataTableColumn } from './types'

interface Row {
  id: number
  name: string
  count: number
}

const rows: Row[] = [
  { id: 1, name: 'apple', count: 3 },
  { id: 2, name: 'banana', count: 5 },
  { id: 3, name: 'cherry', count: 1 },
]

const columns: DataTableColumn<Row>[] = [
  { key: 'name', label: 'Name', sortable: true },
  { key: 'count', label: 'Count', sortable: false },
]

describe('DataTable', () => {
  it('renders one row per item with the configured columns', () => {
    const w = mount(DataTable, { props: { items: rows, columns: columns as never } })
    const tbodyRows = w.findAll('tbody tr')
    expect(tbodyRows.length).toBe(3)
    expect(tbodyRows[0].text()).toContain('apple')
    expect(tbodyRows[2].text()).toContain('cherry')
  })

  it('sets aria-sort on sortable column headers', async () => {
    const w = mount(DataTable, {
      props: { items: rows, columns: columns as never, sortKey: 'name', sortDir: 'asc' },
    })
    const headers = w.findAll('th[scope="col"]')
    expect(headers[0].attributes('aria-sort')).toBe('ascending')
    expect(headers[1].attributes('aria-sort')).toBe('none')
  })

  it('emits sort with next direction when a sortable header is clicked', async () => {
    const w = mount(DataTable, {
      props: { items: rows, columns: columns as never, sortKey: 'name', sortDir: 'asc' },
    })
    const sortBtn = w.find('th[scope="col"] button')
    await sortBtn.trigger('click')
    expect(w.emitted('sort')).toBeTruthy()
    expect(w.emitted('sort')![0]).toEqual(['name', 'desc'])
  })

  it('emits load-more when the button is clicked', async () => {
    const w = mount(DataTable, {
      props: { items: rows, columns: columns as never, nextCursor: 99 },
    })
    const moreBtn = w.findAll('button').find((b) => b.text() === 'Load more')!
    expect(moreBtn).toBeTruthy()
    await moreBtn.trigger('click')
    expect(w.emitted('load-more')).toBeTruthy()
  })

  it('emits row-click on click', async () => {
    const w = mount(DataTable, {
      props: { items: rows, columns: columns as never, interactive: true },
    })
    const tbodyRows = w.findAll('tbody tr')
    await tbodyRows[1].trigger('click')
    expect(w.emitted('row-click')).toBeTruthy()
    expect((w.emitted('row-click')![0][0] as Row).name).toBe('banana')
  })

  it('does not emit row-click for read-only rows', async () => {
    const w = mount(DataTable, { props: { items: rows, columns: columns as never } })
    const tbodyRows = w.findAll('tbody tr')
    await tbodyRows[1].trigger('click')
    expect(w.emitted('row-click')).toBeUndefined()
  })

  it('renders the empty message when items is empty and not loading', () => {
    const w = mount(DataTable, {
      props: { items: [], columns: columns as never, emptyMessage: 'no rows here' },
    })
    expect(w.text()).toContain('no rows here')
  })

  it('toggles selection on Space keypress', async () => {
    const w = mount(DataTable, {
      props: {
        items: rows,
        columns: columns as never,
        selection: new Set<number>(),
      },
      attachTo: document.body,
    })
    const tbodyRows = w.findAll('tbody tr')
    await tbodyRows[1].trigger('keydown', { key: ' ' })
    expect(w.emitted('selection-change')).toBeTruthy()
    const sel = w.emitted('selection-change')![0][0] as Set<number>
    expect(sel.has(2)).toBe(true)
    w.unmount()
  })

  it('navigates rows with ArrowDown', async () => {
    const w = mount(DataTable, {
      props: { items: rows, columns: columns as never, interactive: true },
      attachTo: document.body,
    })
    const tbodyRows = w.findAll('tbody tr')
    ;(tbodyRows[0].element as HTMLElement).focus()
    await tbodyRows[0].trigger('keydown', { key: 'ArrowDown' })
    expect(document.activeElement).toBe(tbodyRows[1].element)
    w.unmount()
  })
})
