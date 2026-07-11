<script setup lang="ts">
// SubNav — accessible section navigation for dense console pages.
//
// Renders grouped `{ key, label, icon?, count? }` items as a vertical rail on
// md+ and a horizontally scrollable strip on small screens. Arrow keys move
// focus between enabled items (Up/Down and Left/Right both work so the control
// behaves whether laid out vertically or horizontally); Home/End jump to the
// ends; Enter/Space selects. Group labels show as overlines on md+ only. The
// host listens to `change` to swap the rendered section.

import { computed } from 'vue'

import UiCountBadge from '@/components/ui/UiCountBadge.vue'
import UiIcon from '@/components/ui/UiIcon.vue'
import { hasIcon } from '@/components/ui/icons'

export interface SubNavItem {
  key: string
  label: string
  icon?: string
  count?: number
  disabled?: boolean
}

export interface SubNavGroup {
  /** Optional overline shown above the group on md+ (hidden on mobile). */
  label?: string | null
  items: SubNavItem[]
}

const props = withDefaults(
  defineProps<{
    groups: SubNavGroup[]
    activeKey: string
    ariaLabel?: string
  }>(),
  { ariaLabel: 'Sections' },
)

const emit = defineEmits<{
  (e: 'change', key: string): void
}>()

const buttons = new Map<string, HTMLButtonElement>()

const flatItems = computed(() => props.groups.flatMap((group) => group.items))
const enabledKeys = computed(() =>
  flatItems.value.filter((item) => !item.disabled).map((item) => item.key),
)

function registerButton(key: string, el: unknown): void {
  if (el instanceof HTMLButtonElement) buttons.set(key, el)
  else buttons.delete(key)
}

function focusKey(key: string | undefined): void {
  if (key) buttons.get(key)?.focus()
}

function move(currentKey: string, direction: 1 | -1): void {
  const keys = enabledKeys.value
  if (keys.length === 0) return
  const current = keys.indexOf(currentKey)
  const next = current === -1 ? keys[0] : keys[(current + direction + keys.length) % keys.length]
  focusKey(next)
}

function onKeydown(event: KeyboardEvent, key: string): void {
  switch (event.key) {
    case 'ArrowDown':
    case 'ArrowRight':
      event.preventDefault()
      move(key, 1)
      break
    case 'ArrowUp':
    case 'ArrowLeft':
      event.preventDefault()
      move(key, -1)
      break
    case 'Home':
      event.preventDefault()
      focusKey(enabledKeys.value[0])
      break
    case 'End':
      event.preventDefault()
      focusKey(enabledKeys.value[enabledKeys.value.length - 1])
      break
    case 'Enter':
    case ' ':
    case 'Spacebar':
      event.preventDefault()
      select(key)
      break
  }
}

function select(key: string): void {
  const item = flatItems.value.find((entry) => entry.key === key)
  if (!item || item.disabled || key === props.activeKey) return
  emit('change', key)
}
</script>

<template>
  <nav :aria-label="ariaLabel" class="min-w-0 max-w-full">
    <div
      role="tablist"
      :aria-label="`${ariaLabel} tabs`"
      class="flex gap-1 overflow-x-auto pb-1 lg:flex-col lg:gap-0.5 lg:overflow-visible lg:pb-0"
    >
      <template v-for="(group, groupIndex) in groups" :key="group.label ?? `group-${groupIndex}`">
        <p
          v-if="group.label"
          class="t-overline hidden shrink-0 px-2.5 pb-1 pt-4 text-fg-subtle first:pt-1 lg:block"
        >
          {{ group.label }}
        </p>
        <button
          v-for="item in group.items"
          :id="`cs-subnav-${item.key}`"
          :key="item.key"
          :ref="(el) => registerButton(item.key, el)"
          type="button"
          role="tab"
          :aria-selected="item.key === activeKey"
          :tabindex="item.key === activeKey ? 0 : -1"
          :disabled="item.disabled"
          class="focus-ring group inline-flex h-9 shrink-0 items-center gap-2.5 rounded-md px-2.5 text-sm font-medium transition-colors duration-fast ease-standard disabled:cursor-not-allowed disabled:text-fg-disabled lg:w-full"
          :class="
            item.key === activeKey
              ? 'bg-accent-subtle text-accent-fg'
              : 'text-fg-muted hover:bg-bg-surface-alt hover:text-fg-default'
          "
          @click="select(item.key)"
          @keydown="onKeydown($event, item.key)"
        >
          <UiIcon
            v-if="hasIcon(item.icon)"
            :name="item.icon"
            class="h-4 w-4 shrink-0"
            :class="
              item.key === activeKey ? 'text-accent-fg' : 'text-fg-subtle group-hover:text-fg-muted'
            "
            aria-hidden="true"
          />
          <span class="truncate">{{ item.label }}</span>
          <UiCountBadge v-if="typeof item.count === 'number'" :value="item.count" class="ml-auto" />
        </button>
      </template>
    </div>
  </nav>
</template>
