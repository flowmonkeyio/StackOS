<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, onUpdated, ref } from 'vue'
import UiButton from './UiButton.vue'
import UiIcon from './UiIcon.vue'
import UiIconButton from './UiIconButton.vue'
import UiPopover from './UiPopover.vue'

export interface FilterChip {
  key: string
  label: string
}
const props = defineProps<{ chips: FilterChip[]; editorLabel?: string; disabled?: boolean }>()
const emit = defineEmits<{ (e: 'edit', key: string): void; (e: 'remove', key: string): void }>()
const root = ref<HTMLElement | null>(null)
const region = ref<HTMLElement | null>(null)
const probe = ref<HTMLElement | null>(null)
const overflowTrigger = ref<{ $el: HTMLElement } | null>(null)
const overflowOpen = ref(false)
const editorOpen = ref(false)
const editorKey = ref<string | null>(null)
const anchor = ref<HTMLElement | null>(null)
const visibleCount = ref(0)
const visible = computed(() => props.chips.slice(0, visibleCount.value))
const hidden = computed(() => props.chips.slice(visibleCount.value))
const fallback = () =>
  root.value?.closest('.ui-filter-bar')?.querySelector<HTMLElement>('button') ?? null
const returnFocus = computed(() => {
  const isVisible = visible.value.some((chip) => chip.key === editorKey.value)
  return anchor.value?.isConnected
    ? anchor.value
    : isVisible
      ? (findChip(region.value) ?? fallback())
      : (overflowTrigger.value?.$el ?? fallback())
})
let observer: ResizeObserver | undefined
function findChip(element: HTMLElement | null | undefined): HTMLElement | null {
  return (
    Array.from(element?.querySelectorAll<HTMLElement>('[data-chip-key]') ?? []).find(
      (chip) => chip.dataset.chipKey === editorKey.value,
    ) ?? null
  )
}
function measure(): void {
  const available = region.value?.clientWidth ?? 0
  let used = 0,
    count = 0
  for (const chip of Array.from(probe.value?.children ?? [])) {
    used += (chip as HTMLElement).getBoundingClientRect().width + (count ? 6 : 0)
    if (used > available) break
    count += 1
  }
  if (visibleCount.value !== count) visibleCount.value = count
}
function syncAnchor(): void {
  if (editorOpen.value && !props.chips.some((chip) => chip.key === editorKey.value)) closeEditor()
  if (editorOpen.value) {
    const current =
      findChip(region.value) ??
      (overflowOpen.value
        ? findChip(overflowTrigger.value?.$el.closest('.ui-popover') as HTMLElement)
        : null) ??
      overflowTrigger.value?.$el ??
      fallback()
    if (current !== anchor.value) anchor.value = current
  }
}
function edit(key: string, event: MouseEvent): void {
  editorKey.value = key
  anchor.value = event.currentTarget as HTMLElement
  overflowOpen.value = Boolean(anchor.value.closest('.ui-filter-chips__chip--overflow'))
  emit('edit', key)
  editorOpen.value = true
}
function remove(key: string): void {
  if (editorKey.value === key) closeEditor()
  emit('remove', key)
}
function closeEditor(): void {
  editorOpen.value = false
}
function close(): void {
  closeEditor()
  overflowOpen.value = false
}
defineExpose({ close })
onMounted(() => {
  if (typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(measure)
    if (region.value) observer.observe(region.value)
    if (probe.value) observer.observe(probe.value)
  }
  measure()
})
onUpdated(() => {
  measure()
  syncAnchor()
})
onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <div
    ref="root"
    class="ui-filter-chips"
  >
    <div
      ref="region"
      class="ui-filter-chips__region"
      @vue:updated="syncAnchor"
    >
      <div
        v-for="chip in visible"
        :key="chip.key"
        class="ui-filter-chips__chip"
      >
        <UiButton
          class="ui-filter-chips__edit"
          variant="ghost"
          size="sm"
          :title="chip.label"
          :aria-label="`Edit ${chip.label}`"
          :data-chip-key="chip.key"
          :disabled="disabled"
          @click="edit(chip.key, $event)"
        >
          {{ chip.label }}
        </UiButton>
        <UiIconButton
          class="ui-filter-chips__remove"
          size="sm"
          :aria-label="`Remove ${chip.label}`"
          :disabled="disabled"
          @click="remove(chip.key)"
        >
          <UiIcon
            name="close"
            class="h-3 w-3"
          />
        </UiIconButton>
      </div>
    </div>
    <div class="ui-filter-chips__overflow">
      <UiPopover
        v-if="hidden.length"
        v-model="overflowOpen"
        placement="bottom-end"
        aria-label="More filters"
        :width="300"
        :close-on-click-outside="!editorOpen"
        :close-on-esc="!editorOpen"
      >
        <template #trigger="{ toggle, open }">
          <UiButton
            ref="overflowTrigger"
            size="sm"
            variant="ghost"
            data-popover-trigger
            :aria-expanded="open"
            :disabled="disabled"
            @click="toggle"
          >
            +{{ hidden.length }} filters
          </UiButton>
        </template>
        <div class="grid gap-1 p-1">
          <div
            v-for="chip in hidden"
            :key="chip.key"
            class="ui-filter-chips__chip ui-filter-chips__chip--overflow"
          >
            <UiButton
              class="ui-filter-chips__edit"
              variant="ghost"
              size="sm"
              :title="chip.label"
              :aria-label="`Edit ${chip.label}`"
              :data-chip-key="chip.key"
              :disabled="disabled"
              @click="edit(chip.key, $event)"
            >
              {{ chip.label }}
            </UiButton>
            <UiIconButton
              class="ui-filter-chips__remove"
              size="sm"
              :aria-label="`Remove ${chip.label}`"
              :disabled="disabled"
              @click="remove(chip.key)"
            >
              <UiIcon
                name="close"
                class="h-3 w-3"
              />
            </UiIconButton>
          </div>
        </div>
      </UiPopover>
    </div>
    <UiPopover
      v-model="editorOpen"
      class="ui-filter-chips__editor"
      :anchor="anchor"
      :return-focus="returnFocus"
      :aria-label="editorLabel ?? 'Edit filter'"
      :width="280"
    >
      <slot
        v-if="editorKey"
        name="editor"
        :filter-key="editorKey"
        :close="closeEditor"
      />
    </UiPopover>
    <div
      ref="probe"
      class="ui-filter-chips__probe"
      aria-hidden="true"
      inert
    >
      <span
        v-for="chip in chips"
        :key="chip.key"
        class="ui-filter-chips__measure"
      >{{
        chip.label
      }}</span>
    </div>
  </div>
</template>

<style scoped>
.ui-filter-chips {
  display: flex;
  flex: 1 1 0;
  gap: 8px;
  min-width: 0;
  position: relative;
}
.ui-filter-chips__region {
  display: flex;
  flex: 1 1 0;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.ui-filter-chips__chip {
  display: flex;
  flex: none;
  align-items: center;
  max-width: 240px;
  height: 28px;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-sm);
  background: var(--color-bg-surface-alt);
}
.ui-filter-chips__chip :deep(.ui-filter-chips__edit) {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  display: block;
  padding: 0 8px;
}
.ui-filter-chips__chip :deep(.ui-button__label) {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ui-filter-chips__chip :deep(.ui-filter-chips__remove) {
  flex: none;
}
.ui-filter-chips__chip--overflow {
  width: 100%;
  max-width: 100%;
}
.ui-filter-chips__chip--overflow :deep(.ui-filter-chips__edit) {
  flex: 1;
  text-align: left;
}
.ui-filter-chips__overflow {
  flex: 0 0 92px;
}
.ui-filter-chips__editor {
  position: absolute;
}
.ui-filter-chips__probe {
  position: absolute;
  visibility: hidden;
  pointer-events: none;
  display: flex;
  gap: 6px;
  height: 0;
  overflow: hidden;
  max-width: 100%;
}
.ui-filter-chips__measure {
  display: inline-block;
  flex: none;
  max-width: 240px;
  padding: 0 44px 0 8px;
  border: 1px solid transparent;
  font-size: 12px;
  font-weight: var(--fw-medium);
  white-space: nowrap;
}
</style>
