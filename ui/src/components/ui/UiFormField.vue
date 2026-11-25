<!--
  UiFormField — wrapper that handles label, help text, error message, and
  required/optional indicator. Wires aria-describedby to the inner control.

  Usage:
    <UiFormField label="Project name" required help="Use kebab-case." :error="errors.name">
      <UiInput v-model="form.name" />
    </UiFormField>
-->
<script setup lang="ts">
import { computed, provide, useId } from 'vue';

import UiIcon from './UiIcon.vue';

export interface UiFormFieldProps {
  label?: string;
  help?: string;
  error?: string | null;
  required?: boolean;
  /** Display "(optional)" instead of nothing when not required. */
  showOptional?: boolean;
  /** When true, label is rendered visually hidden but kept for screen readers. */
  hideLabel?: boolean;
  /** Apply id directly to the slot's first input (label `for`). */
  inputId?: string;
  /** Layout: stacked (default) or inline (label left, control right). */
  layout?: 'stacked' | 'inline';
  /** Mark the field as 'dirty' — useful for unsaved-changes UIs. */
  dirty?: boolean;
  /** Saved indicator pulses briefly when set true. */
  saved?: boolean;
}

const props = withDefaults(defineProps<UiFormFieldProps>(), {
  label: undefined,
  help: undefined,
  error: null,
  inputId: undefined,
  layout: 'stacked',
});

const autoId = useId();
const id = computed(() => props.inputId ?? `ff-${autoId}`);
const helpId = computed(() => `${id.value}-help`);
const errorId = computed(() => `${id.value}-err`);

const describedBy = computed(() => {
  const ids: string[] = [];
  if (props.help) ids.push(helpId.value);
  if (props.error) ids.push(errorId.value);
  return ids.length ? ids.join(' ') : undefined;
});

// Provide to child inputs that opt in via inject('uiFormField').
provide('uiFormField', {
  id,
  describedBy,
  invalid: computed(() => !!props.error),
  required: computed(() => !!props.required),
});
</script>

<template>
  <div
    :class="[
      'ui-form-field',
      layout === 'inline' ? 'grid grid-cols-[200px_1fr] items-start gap-4' : 'flex flex-col gap-1.5',
    ]"
  >
    <div :class="['ui-form-field__label-row flex items-center gap-2', hideLabel && 'sr-only']">
      <label
        v-if="label"
        :for="id"
        class="text-xs font-medium text-fg-default"
      >
        {{ label }}
        <span
          v-if="required"
          class="text-danger"
          aria-hidden="true"
        >*</span>
        <span
          v-else-if="showOptional"
          class="text-fg-subtle font-normal ml-1"
        >(optional)</span>
      </label>
      <span
        v-if="dirty"
        class="inline-flex items-center gap-1 text-2xs font-medium uppercase tracking-wide text-warning-fg"
        aria-label="Unsaved changes"
      >
        <span
          class="h-1.5 w-1.5 shrink-0 rounded-full bg-warning"
          aria-hidden="true"
        />
        unsaved
      </span>
      <span
        v-else-if="saved"
        class="inline-flex items-center gap-1 text-2xs font-medium uppercase tracking-wide text-success-fg"
        aria-live="polite"
      >
        <UiIcon
          name="check"
          class="ui-form-field__status-icon"
        />
        saved
      </span>
    </div>
    <div class="ui-form-field__body flex flex-col gap-1">
      <slot
        :id="id"
        :described-by="describedBy"
        :invalid="!!error"
        :required="!!required"
      />
      <p
        v-if="help && !error"
        :id="helpId"
        class="text-xs text-fg-muted"
      >
        {{ help }}
      </p>
      <p
        v-if="error"
        :id="errorId"
        class="text-xs text-danger-fg flex items-start gap-1"
        role="alert"
      >
        <UiIcon
          name="alert-triangle"
          class="ui-form-field__error-icon mt-0.5 shrink-0"
        />
        <span>{{ error }}</span>
      </p>
    </div>
  </div>
</template>

<style scoped>
.ui-form-field__error-icon {
  width: 12px;
  height: 12px;
  stroke-width: 2;
}
.ui-form-field__status-icon {
  width: 10px;
  height: 10px;
  stroke-width: 2.5;
}
</style>
