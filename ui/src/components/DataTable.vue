<script setup lang="ts" generic="T extends { id: number | string }">
// Generic, accessible data table.
//
// Props match PLAN.md L529-548:
//   - cursor pagination (`nextCursor` + `onLoadMore`)
//   - sortable columns with `aria-sort`
//   - keyboard navigation (arrow keys cycle rows; Space toggles selection)
//   - sticky header
//   - horizontal scroll on small screens
//   - optional row selection with controlled `selection` prop
//
// Generic over `T extends { id: number | string }` so call-sites get
// type inference on `format(value, row)` and on the `onRowClick` payload.

import { computed, ref } from 'vue'

import type { DataTableColumn, DataTableSortDir } from './types'

interface Props {
  items: T[]
  columns: DataTableColumn<T>[]
  loading?: boolean
  nextCursor?: number | null
  /** Optional empty-state message; defaults to "No rows". */
  emptyMessage?: string
  /** Active sort column. */
  sortKey?: string | null
  /** Active sort direction. */
  sortDir?: DataTableSortDir
  /** When provided, renders selection checkboxes. */
  selection?: Set<T['id']>
  /** Stable accessible label for screen readers. */
  ariaLabel?: string
  /** Optional rowKey override; defaults to `row.id`. */
  rowKey?: (row: T) => T['id']
  /** Makes rows keyboard/click navigable. */
  interactive?: boolean
  /** Highlights the active row in read-only master/detail layouts. */
  selectedId?: T['id'] | null
  /** Optional desktop table scroll cap. Keeps long ledgers inside the first viewport. */
  maxHeight?: string
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
  nextCursor: null,
  emptyMessage: 'No rows',
  sortKey: null,
  sortDir: null,
  selection: undefined,
  ariaLabel: 'Data table',
  rowKey: undefined,
  interactive: false,
  selectedId: undefined,
  maxHeight: undefined,
})

const emit = defineEmits<{
  (e: 'load-more'): void
  (e: 'sort', column: string, dir: DataTableSortDir): void
  (e: 'row-click', row: T): void
  (e: 'selection-change', selection: Set<T['id']>): void
}>()

const focusedIndex = ref<number>(-1)

const tbodyRef = ref<HTMLTableSectionElement | null>(null)

const displayItems = computed(() => props.items)

function keyOf(row: T): T['id'] {
  return props.rowKey ? props.rowKey(row) : row.id
}

function ariaSortFor(col: DataTableColumn<T>): 'none' | 'ascending' | 'descending' {
  if (props.sortKey !== col.key || props.sortDir === null) return 'none'
  return props.sortDir === 'asc' ? 'ascending' : 'descending'
}

function nextSortDir(col: DataTableColumn<T>): DataTableSortDir {
  if (props.sortKey !== col.key) return 'asc'
  if (props.sortDir === 'asc') return 'desc'
  if (props.sortDir === 'desc') return null
  return 'asc'
}

function onSortClick(col: DataTableColumn<T>): void {
  if (!col.sortable) return
  emit('sort', col.key, nextSortDir(col))
}

function formatCell(col: DataTableColumn<T>, row: T): string {
  const raw = row[col.key]
  if (col.format) return col.format(raw, row)
  if (raw === null || raw === undefined) return ''
  if (typeof raw === 'boolean') return raw ? 'true' : 'false'
  if (raw instanceof Date) return raw.toISOString()
  return String(raw)
}

function isSelected(row: T): boolean {
  return props.selection?.has(keyOf(row)) ?? false
}

function isActive(row: T): boolean {
  return props.selectedId !== undefined && props.selectedId !== null && keyOf(row) === props.selectedId
}

function toggleSelection(row: T): void {
  if (!props.selection) return
  const next = new Set<T['id']>(props.selection)
  const id = keyOf(row)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  emit('selection-change', next)
}

function focusRow(index: number): void {
  if (index < 0 || index >= displayItems.value.length) return
  focusedIndex.value = index
  const row = tbodyRef.value?.rows.item(index) as HTMLTableRowElement | null
  row?.focus()
}

function onKeydown(e: KeyboardEvent, row: T, index: number): void {
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    focusRow(Math.min(index + 1, displayItems.value.length - 1))
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    focusRow(Math.max(index - 1, 0))
  } else if (e.key === 'Home') {
    e.preventDefault()
    focusRow(0)
  } else if (e.key === 'End') {
    e.preventDefault()
    focusRow(displayItems.value.length - 1)
  } else if (e.key === ' ' || e.key === 'Spacebar') {
    e.preventDefault()
    toggleSelection(row)
  } else if (props.interactive && e.key === 'Enter') {
    e.preventDefault()
    emit('row-click', row)
  }
}

function onCardKeydown(e: KeyboardEvent, row: T): void {
  if (props.interactive && e.key === 'Enter') {
    e.preventDefault()
    emit('row-click', row)
  } else if (e.key === ' ' || e.key === 'Spacebar') {
    e.preventDefault()
    toggleSelection(row)
  }
}
</script>

<template>
  <div class="cs-datatable-wrapper relative">
    <div
      class="hidden overflow-x-auto rounded-md border border-default md:block"
      tabindex="0"
      :style="maxHeight ? { maxHeight, overflowY: 'auto' } : undefined"
    >
      <table
        class="min-w-full divide-y divide-border-subtle text-sm"
        :aria-label="ariaLabel"
        :aria-busy="loading"
        :aria-rowcount="displayItems.length"
      >
        <thead class="sticky top-0 z-10 bg-bg-surface-alt">
          <tr>
            <th
              v-if="selection"
              scope="col"
              class="w-10 px-3 py-2 text-left font-medium"
            >
              <span class="sr-only">Select</span>
            </th>
            <th
              v-for="col in columns"
              :key="col.key"
              scope="col"
              class="whitespace-nowrap px-3 py-2 text-left font-medium text-fg-default"
              :class="[col.widthClass]"
              :aria-sort="ariaSortFor(col)"
            >
              <button
                v-if="col.sortable"
                type="button"
                class="inline-flex items-center gap-1 hover:text-fg-strong focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
                @click="onSortClick(col)"
              >
                {{ col.label }}
                <span
                  aria-hidden="true"
                  class="text-xs"
                >
                  <template v-if="sortKey === col.key && sortDir === 'asc'">▲</template>
                  <template v-else-if="sortKey === col.key && sortDir === 'desc'">▼</template>
                  <template v-else>↕</template>
                </span>
              </button>
              <span v-else>{{ col.label }}</span>
            </th>
          </tr>
        </thead>
        <tbody
          ref="tbodyRef"
          class="divide-y divide-border-subtle bg-bg-surface"
        >
          <tr
            v-for="(row, idx) in displayItems"
            :key="String(keyOf(row))"
            :tabindex="interactive || selection ? 0 : undefined"
            class="hover:bg-bg-surface-alt"
            :class="{
              'bg-accent-subtle': isSelected(row) || isActive(row),
              'cursor-pointer focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-focus': interactive || selection,
            }"
            :aria-selected="isSelected(row)"
            :aria-current="isActive(row) ? 'true' : undefined"
            @click="interactive && emit('row-click', row)"
            @keydown="onKeydown($event, row, idx)"
            @focus="focusedIndex = idx"
          >
            <td
              v-if="selection"
              class="px-3 py-2"
            >
              <input
                type="checkbox"
                :checked="isSelected(row)"
                :aria-label="`Select row ${idx + 1}`"
                class="h-4 w-4 rounded-xs border-border-default accent-accent"
                @click.stop="toggleSelection(row)"
              >
            </td>
            <td
              v-for="col in columns"
              :key="col.key"
              class="px-3 py-2 align-top text-fg-default"
              :class="col.cellClass"
            >
              <slot
                :name="`cell:${col.key}`"
                :row="row"
                :value="row[col.key]"
              >
                {{ formatCell(col, row) }}
              </slot>
            </td>
          </tr>
          <tr v-if="!loading && displayItems.length === 0">
            <td
              :colspan="columns.length + (selection ? 1 : 0)"
              class="px-3 py-8 text-center text-fg-muted"
            >
              {{ emptyMessage }}
            </td>
          </tr>
          <tr v-if="loading && displayItems.length === 0">
            <td
              :colspan="columns.length + (selection ? 1 : 0)"
              class="px-3 py-8 text-center text-fg-muted"
            >
              Loading…
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div
      class="space-y-2 md:hidden"
      :aria-label="ariaLabel"
      :aria-busy="loading"
    >
      <article
        v-for="row in displayItems"
        :key="`mobile-${String(keyOf(row))}`"
        :role="interactive ? 'button' : undefined"
        :tabindex="interactive || selection ? 0 : undefined"
        class="rounded-md border border-default bg-bg-surface p-3 shadow-xs"
        :class="{ 'bg-accent-subtle': isSelected(row) || isActive(row) }"
        :aria-selected="isSelected(row)"
        :aria-current="isActive(row) ? 'true' : undefined"
        @click="interactive && emit('row-click', row)"
        @keydown="onCardKeydown($event, row)"
      >
        <div
          v-if="selection"
          class="mb-2 flex items-center gap-2"
        >
          <input
            type="checkbox"
            :checked="isSelected(row)"
            :aria-label="`Select row ${String(keyOf(row))}`"
            class="h-4 w-4 rounded-xs border-border-default accent-accent"
            @click.stop="toggleSelection(row)"
          >
          <span class="text-xs font-medium uppercase text-fg-subtle">Select</span>
        </div>
        <dl class="grid gap-2">
          <div
            v-for="col in columns"
            :key="`mobile-${String(keyOf(row))}-${col.key}`"
            class="grid grid-cols-[7.5rem_1fr] gap-2 text-sm"
          >
            <dt class="text-xs font-medium uppercase text-fg-subtle">
              {{ col.label }}
            </dt>
            <dd
              class="min-w-0 break-words text-fg-default"
              :class="col.cellClass"
            >
              <slot
                :name="`cell:${col.key}`"
                :row="row"
                :value="row[col.key]"
              >
                {{ formatCell(col, row) }}
              </slot>
            </dd>
          </div>
        </dl>
      </article>
      <div
        v-if="!loading && displayItems.length === 0"
        class="rounded-md border border-dashed border-subtle bg-bg-surface p-6 text-center text-sm text-fg-muted"
      >
        {{ emptyMessage }}
      </div>
      <div
        v-if="loading && displayItems.length === 0"
        class="rounded-md border border-dashed border-subtle bg-bg-surface p-6 text-center text-sm text-fg-muted"
      >
        Loading…
      </div>
    </div>

    <div
      v-if="nextCursor !== null && nextCursor !== undefined"
      class="mt-3 flex justify-center"
    >
      <button
        type="button"
        class="h-8 rounded-sm border border-default bg-bg-surface px-3 text-sm font-medium text-fg-default hover:bg-bg-surface-alt disabled:cursor-not-allowed disabled:opacity-50"
        :disabled="loading"
        @click="emit('load-more')"
      >
        Load more
      </button>
    </div>
  </div>
</template>
