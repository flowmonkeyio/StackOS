<!--
  UiInput — text-like input. Use inside UiFormField for label/error/help.
  Supports size, prefix/suffix slots, invalid/disabled/readonly states.
-->
<script setup lang="ts">
import { computed, inject, useAttrs } from 'vue'
import type { ComputedRef } from 'vue'

import UiIcon from './UiIcon.vue'

defineOptions({ inheritAttrs: false })

export interface UiInputProps {
  modelValue?: string | number | null
  type?: 'text' | 'email' | 'url' | 'tel' | 'search' | 'number' | 'password' | 'date'
  size?: 'sm' | 'md' | 'lg'
  placeholder?: string
  disabled?: boolean
  readonly?: boolean
  invalid?: boolean
  required?: boolean
  /** When true, shows a clear button when there's a value. */
  clearable?: boolean
  /** Stretch to fill parent. */
  block?: boolean
  id?: string
  /** ARIA — wired automatically by UiFormField. */
  ariaDescribedby?: string
}

const props = withDefaults(defineProps<UiInputProps>(), {
  modelValue: null,
  type: 'text',
  size: 'md',
  placeholder: undefined,
  block: true,
  id: undefined,
  ariaDescribedby: undefined,
})

const emit = defineEmits<{
  (e: 'update:modelValue', value: string | number | null): void
  (e: 'change', value: string | number | null): void
  (e: 'blur', ev: FocusEvent): void
  (e: 'focus', ev: FocusEvent): void
  (e: 'clear'): void
}>()

const attrs = useAttrs()
const field = inject<{
  id: ComputedRef<string>
  describedBy: ComputedRef<string | undefined>
  invalid: ComputedRef<boolean>
  required: ComputedRef<boolean>
} | null>('uiFormField', null)

const controlId = computed(() => props.id ?? field?.id.value)
const controlDescribedBy = computed(() => props.ariaDescribedby ?? field?.describedBy.value)
const controlInvalid = computed(() => props.invalid || field?.invalid.value)
const controlRequired = computed(() => props.required || field?.required.value)

const sizeClass = computed(
  () =>
    ({
      sm: 'h-7 text-sm px-2',
      md: 'h-8 text-sm px-2.5',
      lg: 'h-10 text-base px-3',
    })[props.size],
)

function onInput(ev: Event) {
  const target = ev.target as HTMLInputElement
  const value =
    props.type === 'number' ? (target.value === '' ? null : Number(target.value)) : target.value
  emit('update:modelValue', value)
}

function onChange(ev: Event) {
  const target = ev.target as HTMLInputElement
  const value =
    props.type === 'number' ? (target.value === '' ? null : Number(target.value)) : target.value
  emit('change', value)
}

function clear() {
  emit('update:modelValue', '')
  emit('clear')
}
</script>

<template>
  <div
    :class="[
      'ui-input focus-within:outline focus-within:outline-2 focus-within:outline-focus focus-within:outline-offset-2 inline-flex items-center rounded-sm border bg-bg-surface shadow-xs transition-colors duration-fast',
      invalid
        ? 'border-danger'
        : 'border-default hover:border-strong hover:bg-bg-surface-alt focus-within:border-accent focus-within:bg-bg-surface',
      disabled &&
        'bg-bg-sunken text-fg-disabled cursor-not-allowed opacity-70 hover:border-default hover:bg-bg-sunken',
      readonly && 'bg-bg-surface-alt',
      block && 'w-full',
      sizeClass,
    ]"
  >
    <span
      v-if="$slots.prefix"
      class="ui-input__prefix flex items-center text-fg-subtle pr-1.5"
    >
      <slot name="prefix" />
    </span>
    <input
      v-bind="attrs"
      :id="controlId"
      :type="type"
      :value="modelValue ?? ''"
      :placeholder="placeholder"
      :disabled="disabled"
      :readonly="readonly"
      :required="controlRequired"
      :aria-invalid="controlInvalid || undefined"
      :aria-describedby="controlDescribedBy"
      class="ui-input__field h-full flex-1 min-w-0 border-0 bg-transparent text-fg-default outline-none placeholder:text-fg-disabled disabled:cursor-not-allowed disabled:text-fg-disabled"
      @input="onInput"
      @change="onChange"
      @blur="$emit('blur', $event)"
      @focus="$emit('focus', $event)"
    >
    <button
      v-if="clearable && modelValue && !disabled && !readonly"
      type="button"
      class="ui-input__clear flex items-center justify-center w-4 h-4 ml-1 text-fg-subtle hover:text-fg-default rounded-xs"
      aria-label="Clear input"
      @click="clear"
    >
      <UiIcon
        name="close"
        class="h-3 w-3"
      />
    </button>
    <span
      v-if="$slots.suffix"
      class="ui-input__suffix flex items-center text-fg-subtle pl-1.5"
    >
      <slot name="suffix" />
    </span>
  </div>
</template>
