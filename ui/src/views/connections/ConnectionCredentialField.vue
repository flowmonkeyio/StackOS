<script setup lang="ts">
import { UiCheckbox, UiFormField, UiInput, UiSecretInput, UiSelect } from '@/components/ui'

import { connectionFieldInputId } from './fieldIds'
import type { AuthField } from './types'

const props = defineProps<{
  field: AuthField
  modelValue: string
  inputType: 'text' | 'url' | 'number' | 'email'
  secret: boolean
  select: boolean
  options: Array<{ value: string; label: string }>
  error?: string
  editing?: boolean
  secretPresent?: boolean
}>()

const emit = defineEmits<{
  (event: 'update:modelValue', value: string | number | null): void
}>()

function selectedValues(): Set<string> {
  return new Set(
    props.modelValue
      .split(',')
      .map((value) => value.trim())
      .filter(Boolean),
  )
}

function updateMultiSelect(value: string, selected: boolean): void {
  const values = selectedValues()
  if (selected) values.add(value)
  else values.delete(value)
  const ordered = props.options
    .map((option) => option.value)
    .filter((optionValue) => values.has(optionValue))
  emit('update:modelValue', ordered.join(','))
}
</script>

<template>
  <UiFormField
    :label="field.label"
    :saved="editing && secret && secretPresent && modelValue.trim() === ''"
    :help="
      editing && secret && secretPresent
        ? [field.description, 'Saved — leave blank to keep it.'].filter(Boolean).join(' ')
        : (field.description ?? undefined)
    "
    :required="field.required && !(editing && secret && secretPresent)"
    :input-id="connectionFieldInputId(field.key)"
    :error="error"
  >
    <template #default="{ id, describedBy, invalid }">
      <div
        v-if="field.type === 'multi-select' || field.type === 'multiselect'"
        :id="id"
        class="grid gap-2 rounded-sm border border-default bg-bg-surface p-3"
        role="group"
        :aria-describedby="describedBy"
        :aria-invalid="invalid || undefined"
      >
        <UiCheckbox
          v-for="option in options"
          :key="option.value"
          :model-value="selectedValues().has(option.value)"
          :label="option.label"
          @update:model-value="updateMultiSelect(option.value, $event)"
        />
      </div>
      <UiSelect
        v-else-if="select"
        :id="id"
        :model-value="modelValue"
        :options="options"
        :aria-describedby="describedBy"
        :invalid="invalid"
        :placeholder="field.placeholder ?? 'Select'"
        @update:model-value="emit('update:modelValue', $event)"
      />
      <UiSecretInput
        v-else-if="secret"
        :id="id"
        :model-value="modelValue"
        :aria-describedby="describedBy"
        :invalid="invalid"
        no-copy
        no-reveal
        :placeholder="editing && secretPresent ? '••••••••' : (field.placeholder ?? '')"
        @update:model-value="emit('update:modelValue', $event)"
      />
      <UiInput
        v-else
        :id="id"
        :model-value="modelValue"
        :type="inputType"
        :aria-describedby="describedBy"
        :invalid="invalid"
        :placeholder="field.placeholder ?? undefined"
        @update:model-value="emit('update:modelValue', $event)"
      />
    </template>
  </UiFormField>
</template>
