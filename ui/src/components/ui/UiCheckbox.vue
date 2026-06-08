<!--
  UiCheckbox — supports indeterminate state.
-->
<script setup lang="ts">
import { computed } from 'vue'

export interface UiCheckboxProps {
  modelValue?: boolean | null
  indeterminate?: boolean
  disabled?: boolean
  invalid?: boolean
  required?: boolean
  size?: 'sm' | 'md'
  id?: string
  /** Inline label text. Use slot for richer content. */
  label?: string
  /** Help / description below label. */
  description?: string
}

const props = withDefaults(defineProps<UiCheckboxProps>(), {
  modelValue: null,
  size: 'md',
  id: undefined,
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
</script>

<template>
  <label
    :class="[
      'ui-checkbox inline-flex items-start gap-2 cursor-pointer select-none',
      disabled && 'opacity-60 cursor-not-allowed',
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
        class="peer sr-only"
        @change="onChange"
      >
      <span
        :class="[
          'ui-checkbox__box flex items-center justify-center rounded-xs border transition-colors duration-fast',
          boxSize,
          modelValue || indeterminate
            ? 'bg-accent border-accent text-fg-on-accent'
            : invalid
              ? 'bg-bg-surface border-danger'
              : 'bg-bg-surface border-strong',
          'peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-focus peer-focus-visible:outline-offset-2',
        ]"
        aria-hidden="true"
      >
        <svg
          v-if="modelValue && !indeterminate"
          width="10"
          height="10"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="3.5"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <path d="m5 12 5 5 9-12" />
        </svg>
        <svg
          v-else-if="indeterminate"
          width="10"
          height="10"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="3.5"
          stroke-linecap="round"
        >
          <path d="M5 12h14" />
        </svg>
      </span>
    </span>
    <span
      v-if="label || description || $slots.default"
      class="ui-checkbox__text flex flex-col"
    >
      <span class="text-sm text-fg-default leading-tight">
        <slot>{{ label }}</slot>
      </span>
      <span
        v-if="description"
        class="text-xs text-fg-muted leading-tight mt-0.5"
      >{{
        description
      }}</span>
    </span>
  </label>
</template>
