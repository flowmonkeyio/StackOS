<!--
  UiCheckbox — supports indeterminate state.
-->
<script setup lang="ts">
import { computed } from 'vue'

import UiIcon from './UiIcon.vue'

export interface UiCheckboxProps {
  modelValue?: boolean | null
  indeterminate?: boolean
  disabled?: boolean
  invalid?: boolean
  required?: boolean
  size?: 'sm' | 'md'
  id?: string
  /** Help or error text associated with the native checkbox. */
  ariaDescribedby?: string
  /** Inline label text. Use slot for richer content. */
  label?: string
  /** Help / description below label. */
  description?: string
}

const props = withDefaults(defineProps<UiCheckboxProps>(), {
  modelValue: null,
  size: 'md',
  id: undefined,
  ariaDescribedby: undefined,
  label: undefined,
  description: undefined,
})

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'change', value: boolean): void
}>()

function onChange(ev: Event) {
  const v = (ev.target as HTMLInputElement).checked
  emit('update:modelValue', v)
  emit('change', v)
}

const boxSize = computed(() => (props.size === 'sm' ? 'w-3.5 h-3.5' : 'w-4 h-4'))

const boxStateClass = computed(() => {
  if (props.disabled) {
    return props.modelValue || props.indeterminate
      ? 'bg-fg-disabled border-fg-disabled text-fg-inverse'
      : 'bg-bg-sunken border-default'
  }
  if (props.modelValue || props.indeterminate) return 'bg-accent border-accent text-fg-on-accent'
  if (props.invalid) return 'bg-bg-surface border-danger'
  return 'bg-bg-surface border-strong'
})
</script>

<template>
  <label
    :class="[
      'ui-checkbox inline-flex items-start gap-2 select-none',
      disabled ? 'cursor-not-allowed' : 'cursor-pointer',
    ]"
  >
    <span class="relative inline-flex items-center justify-center mt-0.5">
      <input
        :id="id"
        type="checkbox"
        :checked="!!modelValue"
        :indeterminate.prop="!!indeterminate"
        :disabled="disabled"
        :required="required"
        :aria-invalid="invalid || undefined"
        :aria-describedby="ariaDescribedby"
        class="peer sr-only"
        @change="onChange"
      >
      <span
        :class="[
          'ui-checkbox__box flex items-center justify-center rounded-xs border transition-colors duration-fast',
          boxSize,
          boxStateClass,
          'peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-focus peer-focus-visible:outline-offset-2',
        ]"
        aria-hidden="true"
      >
        <UiIcon
          v-if="modelValue && !indeterminate"
          name="check"
          class="ui-checkbox__check"
        />
        <span
          v-else-if="indeterminate"
          class="ui-checkbox__dash h-0.5 w-2 rounded-full bg-current"
        />
      </span>
    </span>
    <span
      v-if="label || description || $slots.default"
      class="ui-checkbox__text flex flex-col"
    >
      <span
        :class="['text-sm leading-tight', disabled ? 'text-fg-disabled' : 'text-fg-default']"
      >
        <slot>{{ label }}</slot>
      </span>
      <span
        v-if="description"
        :class="['text-xs leading-tight mt-0.5', disabled ? 'text-fg-disabled' : 'text-fg-muted']"
      >{{
        description
      }}</span>
    </span>
  </label>
</template>

<style scoped>
.ui-checkbox__check {
  width: 10px;
  height: 10px;
  stroke-width: 3.5;
}
</style>
