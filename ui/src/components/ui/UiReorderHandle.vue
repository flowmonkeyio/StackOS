<script setup lang="ts">
import UiDropdownMenu from './UiDropdownMenu.vue'
import UiIcon from './UiIcon.vue'
import type { ReorderHandleBindings } from './verticalReorder'

const props = defineProps<ReorderHandleBindings>()
function lift(event: DragEvent, open: boolean, toggle: () => void): void {
  if (open) toggle()
  props.onLift(event)
}
</script>

<template>
  <UiDropdownMenu
    class="ui-reorder-handle"
    :items="items"
    :aria-label="label"
  >
    <template #trigger="{ toggle, open }">
      <button
        type="button"
        class="ui-reorder-handle__button focus-ring"
        data-dropdown-trigger
        data-reorder-handle
        :draggable="!disabled"
        :disabled="disabled"
        :aria-label="label"
        :aria-expanded="open"
        aria-haspopup="menu"
        @click="toggle"
        @dragstart="lift($event, open, toggle)"
        @dragend="onFinish"
      >
        <UiIcon
          name="dots-vertical"
          class="ui-reorder-handle__dots"
        />
        <UiIcon
          name="dots-vertical"
          class="ui-reorder-handle__dots"
        />
      </button>
    </template>
  </UiDropdownMenu>
</template>

<style scoped>
.ui-reorder-handle { display: inline-flex; align-items: center; flex: none; height: 20px; vertical-align: middle; }
.ui-reorder-handle__button { position: relative; display: inline-flex; align-items: center; justify-content: center; height: 20px; width: 14px; flex: none; border-radius: var(--radius-xs); cursor: grab; }
.ui-reorder-handle__button:active { cursor: grabbing; }
.ui-reorder-handle__button:disabled { cursor: wait; }
.ui-reorder-handle__dots { position: absolute; top: 2px; left: -4px; width: 16px; height: 16px; }
.ui-reorder-handle__dots:last-child { left: 2px; }
</style>
