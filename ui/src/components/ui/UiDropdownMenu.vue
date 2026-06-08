<!--
  UiDropdownMenu — anchored menu of actions. Keyboard-navigable.
  Items can be commands, links, separators, or nested groups.
-->
<script setup lang="ts">
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'

import { hasIcon } from './icons'
import UiIcon from './UiIcon.vue'

export interface DropdownItem {
  /** Stable key. */
  key: string
  label?: string
  /** Icon registry name. */
  icon?: string
  /** Right-aligned hint, e.g. "⌘K". */
  shortcut?: string
  /** When `as: 'link'`, navigate via href. */
  href?: string
  /** When `as: 'separator'`, ignore label. */
  as?: 'item' | 'link' | 'separator' | 'header'
  disabled?: boolean
  /** Tone — used for destructive items. */
  tone?: 'default' | 'danger'
  onSelect?: () => void
}

export interface UiDropdownMenuProps {
  items: DropdownItem[]
  placement?: 'bottom-start' | 'bottom-end' | 'top-start' | 'top-end'
  ariaLabel?: string
  /** Width — fits content by default. */
  width?: number | string
}

const props = withDefaults(defineProps<UiDropdownMenuProps>(), {
  placement: 'bottom-start',
  ariaLabel: undefined,
  width: undefined,
})

const emit = defineEmits<{
  (e: 'select', item: DropdownItem): void
}>()

const open = ref(false)
const root = ref<HTMLElement | null>(null)
const panel = ref<HTMLElement | null>(null)
const activeIndex = ref(-1)

const focusableItems = computed(() =>
  props.items
    .map((item, i) => ({ item, i }))
    .filter(({ item }) => !item.disabled && item.as !== 'separator' && item.as !== 'header'),
)

function setOpen(v: boolean) {
  open.value = v
  if (v) {
    activeIndex.value = focusableItems.value[0]?.i ?? -1
  }
}

function onTriggerClick() {
  setOpen(!open.value)
}

function move(delta: number) {
  if (!focusableItems.value.length) return
  const cur = focusableItems.value.findIndex(({ i }) => i === activeIndex.value)
  const next = (cur + delta + focusableItems.value.length) % focusableItems.value.length
  activeIndex.value = focusableItems.value[next].i
}

function selectActive() {
  const item = props.items[activeIndex.value]
  if (item) select(item)
}

function select(item: DropdownItem) {
  if (item.disabled || item.as === 'separator' || item.as === 'header') return
  item.onSelect?.()
  emit('select', item)
  setOpen(false)
  ;(root.value?.querySelector('[data-dropdown-trigger]') as HTMLElement | null)?.focus()
}

function onKey(ev: KeyboardEvent) {
  if (!open.value) return
  if (ev.key === 'Escape') {
    setOpen(false)
    ev.preventDefault()
  } else if (ev.key === 'ArrowDown') {
    move(1)
    ev.preventDefault()
  } else if (ev.key === 'ArrowUp') {
    move(-1)
    ev.preventDefault()
  } else if (ev.key === 'Enter' || ev.key === ' ') {
    selectActive()
    ev.preventDefault()
  } else if (ev.key === 'Home') {
    activeIndex.value = focusableItems.value[0]?.i ?? -1
    ev.preventDefault()
  } else if (ev.key === 'End') {
    activeIndex.value = focusableItems.value.at(-1)?.i ?? -1
    ev.preventDefault()
  }
}

function onDocClick(ev: MouseEvent) {
  if (open.value && root.value && !root.value.contains(ev.target as Node)) setOpen(false)
}

onMounted(() => {
  document.addEventListener('keydown', onKey)
  document.addEventListener('mousedown', onDocClick)
})
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKey)
  document.removeEventListener('mousedown', onDocClick)
})

function onPanelMounted() {
  panel.value?.focus()
}

const placementClass = computed(
  () =>
    ({
      'bottom-start': 'top-full left-0 mt-1',
      'bottom-end': 'top-full right-0 mt-1',
      'top-start': 'bottom-full left-0 mb-1',
      'top-end': 'bottom-full right-0 mb-1',
    })[props.placement],
)
</script>

<template>
  <div ref="root" class="ui-dropdown relative inline-block">
    <slot name="trigger" :open="open" :toggle="onTriggerClick" />
    <transition
      enter-active-class="transition duration-fast ease-enter"
      enter-from-class="opacity-0 scale-[0.98]"
      leave-active-class="transition duration-fast ease-exit"
      leave-to-class="opacity-0 scale-[0.98]"
    >
      <div
        v-if="open"
        ref="panel"
        tabindex="-1"
        role="menu"
        :aria-label="ariaLabel"
        @vue:mounted="onPanelMounted"
        :class="[
          'ui-dropdown__panel absolute z-dropdown min-w-[180px] py-1 rounded-md border border-default bg-bg-surface shadow-md focus:outline-none',
          placementClass,
        ]"
        :style="{ width: typeof width === 'number' ? width + 'px' : width }"
      >
        <template v-for="(item, i) in items" :key="item.key">
          <div
            v-if="item.as === 'separator'"
            role="separator"
            class="my-1 border-t border-subtle"
          />
          <div
            v-else-if="item.as === 'header'"
            role="presentation"
            class="px-3 py-1.5 text-2xs font-semibold uppercase text-fg-subtle"
          >
            {{ item.label }}
          </div>
          <component
            :is="item.as === 'link' ? 'a' : 'button'"
            v-else
            :type="item.as === 'link' ? undefined : 'button'"
            :href="item.as === 'link' ? item.href : undefined"
            role="menuitem"
            :tabindex="-1"
            :disabled="item.disabled || undefined"
            :aria-disabled="item.disabled || undefined"
            :class="[
              'ui-dropdown__item w-full flex items-center gap-2 px-3 py-1.5 text-sm text-left',
              item.tone === 'danger' ? 'text-danger-fg' : 'text-fg-default',
              item.disabled
                ? 'opacity-50 cursor-not-allowed'
                : 'hover:bg-bg-surface-alt cursor-pointer',
              activeIndex === i && !item.disabled && 'bg-bg-surface-alt',
            ]"
            @click="select(item)"
            @mouseenter="!item.disabled && (activeIndex = i)"
          >
            <span v-if="$slots.icon || hasIcon(item.icon)" class="shrink-0 text-fg-muted">
              <slot name="icon" :item="item">
                <UiIcon :name="item.icon" class="ui-dropdown__icon" />
              </slot>
            </span>
            <span class="flex-1 truncate">{{ item.label }}</span>
            <span v-if="item.shortcut" class="text-2xs text-fg-subtle font-mono">{{
              item.shortcut
            }}</span>
          </component>
        </template>
      </div>
    </transition>
  </div>
</template>

<style scoped>
.ui-dropdown__icon {
  width: 14px;
  height: 14px;
  flex: none;
}
</style>
