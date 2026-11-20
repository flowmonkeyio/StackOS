<!--
  UiFilterBar — opinionated filter cluster. Search input + named filter
  controls + a "clear all" affordance + active-filter chips.

  Filters are described declaratively; the consumer owns state.
-->
<script setup lang="ts">
import { ref } from 'vue'
import UiInput from './UiInput.vue'
import UiBadge from './UiBadge.vue'
import UiButton from './UiButton.vue'
import UiIcon from './UiIcon.vue'
import UiFilterChipStrip, { type FilterChip } from './UiFilterChipStrip.vue'

export interface ActiveFilter {
  key: string
  label: string
  /** Human display of value. */
  value: string
}

defineProps<{
  /** Search input value (two-way). */
  search?: string
  searchPlaceholder?: string
  /** Active filter chips — caller renders the controls themselves in the slot. */
  active?: ActiveFilter[]
  ariaLabel?: string
  /** Hide the search box. */
  noSearch?: boolean
  /** Optional single-row presentation with editable, responsive applied chips. */
  editableActive?: FilterChip[]
  chipEditorLabel?: string
  disabled?: boolean
}>()

defineEmits<{
  (e: 'update:search', v: string): void
  (e: 'remove', key: string): void
  (e: 'clearAll'): void
  (e: 'edit', key: string): void
}>()
const chipStrip = ref<InstanceType<typeof UiFilterChipStrip> | null>(null)
defineExpose({ closeEditor: () => chipStrip.value?.close() })
</script>

<template>
  <div
    role="region"
    :aria-label="ariaLabel ?? 'Filters'"
    class="ui-filter-bar flex flex-col gap-2"
  >
    <div
      :class="['flex items-center gap-2', editableActive ? 'ui-filter-bar__inline' : 'flex-wrap']"
    >
      <UiInput
        v-if="!noSearch"
        :model-value="search"
        :placeholder="searchPlaceholder ?? 'Search…'"
        size="sm"
        clearable
        :block="false"
        class="flex-1 min-w-[200px] max-w-sm"
        @update:model-value="(v: any) => $emit('update:search', String(v ?? ''))"
      >
        <template #prefix>
          <UiIcon
            name="search"
            class="h-3.5 w-3.5"
            aria-hidden="true"
          />
        </template>
      </UiInput>
      <slot />
      <UiFilterChipStrip
        v-if="editableActive"
        ref="chipStrip"
        :chips="editableActive"
        :editor-label="chipEditorLabel"
        :disabled="disabled"
        @edit="$emit('edit', $event)"
        @remove="$emit('remove', $event)"
      >
        <template #editor="scope">
          <slot
            name="editor"
            v-bind="scope"
          />
        </template>
      </UiFilterChipStrip>
      <div class="ml-auto flex items-center gap-2">
        <slot name="right" />
      </div>
    </div>
    <div
      v-if="active && active.length"
      class="flex items-center flex-wrap gap-1.5"
    >
      <UiBadge
        v-for="f in active"
        :key="f.key"
        tone="info"
        variant="subtle"
        size="sm"
        interactive
        truncate
        :aria-label="`Remove ${f.label} filter: ${f.value}`"
        :title="`${f.label}: ${f.value}`"
        @click="$emit('remove', f.key)"
      >
        <span class="text-fg-muted font-normal mr-1">{{ f.label }}:</span>
        <span>{{ f.value }}</span>
        <template #iconRight>
          <UiIcon
            name="close"
            class="-mr-0.5 h-2.5 w-2.5 shrink-0"
            aria-hidden="true"
          />
        </template>
      </UiBadge>
      <UiButton
        variant="link"
        size="sm"
        @click="$emit('clearAll')"
      >
        Clear all
      </UiButton>
    </div>
  </div>
</template>

<style scoped>
.ui-filter-bar__inline {
  flex-wrap: nowrap;
  min-width: 0;
}
.ui-filter-bar__inline :deep(.ui-popover) {
  flex: none;
}
.ui-filter-bar__inline :deep(.ui-button) {
  white-space: nowrap;
}
</style>
