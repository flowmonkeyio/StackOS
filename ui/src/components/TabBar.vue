<script setup lang="ts">
// TabBar — accessible workflow navigation with arrow-key support.
//
// `tabs` is an ordered list of `{ key, label, count?, disabled? }` items.
// `activeKey` selects which tab is rendered as `aria-selected="true"`. The
// host listens to `change` to swap content. Vertical accent bar marks
// the active tab; pending/M5.B tabs can pass `count` for a numeric badge.

import { computed, ref } from 'vue'

import { UiSelect } from '@/components/ui'

interface Tab {
  key: string
  label: string
  group?: string
  count?: number
  disabled?: boolean
}

interface Props {
  tabs: Tab[]
  activeKey: string
  /** Aria-label for the surrounding tablist. */
  ariaLabel?: string
}

interface GroupedTab {
  tab: Tab
  index: number
}

interface TabGroup {
  name: string | null
  items: GroupedTab[]
}

const props = withDefaults(defineProps<Props>(), {
  ariaLabel: 'Tabs',
})

const emit = defineEmits<{
  (e: 'change', key: string): void
}>()

const tabRefs = ref<HTMLButtonElement[]>([])
const focusIdx = ref<number | null>(null)

const tabGroups = computed<TabGroup[]>(() => {
  const groups: TabGroup[] = []
  for (const [index, tab] of props.tabs.entries()) {
    const name = tab.group ?? null
    const current = groups[groups.length - 1]
    if (!current || current.name !== name) {
      groups.push({ name, items: [{ tab, index }] })
    } else {
      current.items.push({ tab, index })
    }
  }
  return groups
})

const hasNamedGroups = computed(() => tabGroups.value.some((group) => group.name !== null))

const activeIndex = computed(() => {
  const index = props.tabs.findIndex((tab) => tab.key === props.activeKey)
  return index >= 0 ? index : 0
})

const activeTabGroup = computed<TabGroup>(() => {
  return (
    tabGroups.value.find((group) => group.items.some(({ tab }) => tab.key === props.activeKey)) ??
    tabGroups.value[0] ?? { name: null, items: [] }
  )
})

const visibleTabItems = computed<GroupedTab[]>(() =>
  hasNamedGroups.value
    ? activeTabGroup.value.items
    : props.tabs.map((tab, index) => ({ tab, index })),
)

const groupOptions = computed(() =>
  tabGroups.value.map((group) => ({
    value: group.name ?? '',
    label: group.name ?? 'Sections',
  })),
)

function focusTab(i: number): void {
  focusIdx.value = i
  const el = tabRefs.value[i]
  if (el) el.focus()
}

function nextEnabled(start: number, dir: 1 | -1): number {
  const items = visibleTabItems.value
  const n = items.length
  if (n === 0) return start
  const startPosition = Math.max(
    0,
    items.findIndex((item) => item.index === start),
  )
  for (let step = 1; step <= n; step++) {
    const position = (startPosition + dir * step + n) % n
    const item = items[position]
    if (!item.tab.disabled) return item.index
  }
  return start
}

function firstEnabled(dir: 1 | -1): number {
  const items = visibleTabItems.value
  const ordered = dir === 1 ? items : [...items].reverse()
  return ordered.find((item) => !item.tab.disabled)?.index ?? focusIdx.value ?? activeIndex.value
}

function onKeydown(e: KeyboardEvent, idx: number): void {
  if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
    e.preventDefault()
    focusTab(nextEnabled(idx, 1))
  } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
    e.preventDefault()
    focusTab(nextEnabled(idx, -1))
  } else if (e.key === 'Home') {
    e.preventDefault()
    focusTab(firstEnabled(1))
  } else if (e.key === 'End') {
    e.preventDefault()
    focusTab(firstEnabled(-1))
  } else if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
    e.preventDefault()
    onSelect(props.tabs[idx])
  }
}

function onSelect(tab: Tab): void {
  if (tab.disabled) return
  if (tab.key === props.activeKey) return
  emit('change', tab.key)
}

function onGroupChange(value: string | number | null): void {
  const nextName = String(value ?? '')
  const nextGroup = tabGroups.value.find((group) => (group.name ?? '') === nextName)
  const firstTab = nextGroup?.items.find(({ tab }) => !tab.disabled)?.tab
  if (firstTab) onSelect(firstTab)
}
</script>

<template>
  <nav
    :aria-label="props.ariaLabel"
    class="border-b border-subtle"
  >
    <div class="flex flex-wrap items-center gap-x-4 gap-y-2">
      <label
        v-if="hasNamedGroups"
        class="flex items-center gap-2 pb-2 text-sm"
      >
        <span class="t-overline shrink-0 text-fg-subtle">
          Stage
        </span>
        <UiSelect
          :model-value="activeTabGroup.name ?? ''"
          :options="groupOptions"
          size="sm"
          :block="false"
          class="min-w-28"
          @update:model-value="onGroupChange"
        />
      </label>

      <div
        role="tablist"
        :aria-label="`${props.ariaLabel} tabs`"
        class="-mb-px flex min-w-0 flex-wrap items-center gap-1"
      >
        <button
          v-for="{ tab, index } in visibleTabItems"
          :id="`cs-tab-${tab.key}`"
          :key="tab.key"
          :ref="(el) => (tabRefs[index] = el as HTMLButtonElement)"
          type="button"
          role="tab"
          :aria-selected="tab.key === props.activeKey"
          :tabindex="tab.key === props.activeKey ? 0 : -1"
          :disabled="tab.disabled"
          class="focus-ring-inset inline-flex h-9 items-center justify-center gap-1.5 border-b-2 px-3 text-sm font-medium transition-colors duration-fast disabled:cursor-not-allowed disabled:text-fg-disabled"
          :class="
            tab.key === props.activeKey
              ? 'border-accent text-fg-strong'
              : 'border-transparent text-fg-muted hover:border-strong hover:text-fg-default'
          "
          @click="onSelect(tab)"
          @keydown="onKeydown($event, index)"
        >
          <span class="truncate">{{ tab.label }}</span>
          <span
            v-if="typeof tab.count === 'number'"
            class="inline-block min-w-[1.25rem] rounded-full bg-bg-sunken px-1.5 py-0.5 text-center text-2xs font-medium tabular-nums text-fg-muted"
          >
            {{ tab.count }}
          </span>
        </button>
      </div>
    </div>
  </nav>
</template>
