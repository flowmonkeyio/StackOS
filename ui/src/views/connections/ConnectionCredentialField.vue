<script setup lang="ts">
import { UiFormField, UiInput, UiSecretInput, UiSelect } from '@/components/ui'

import { connectionFieldInputId } from './fieldIds'
import type { AuthField } from './types'

defineProps<{
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

defineEmits<{
  (event: 'update:modelValue', value: string | number | null): void
}>()
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
      <UiSelect
        v-if="select"
        :id="id"
        :model-value="modelValue"
        :options="options"
        :aria-describedby="describedBy"
        :invalid="invalid"
        :placeholder="field.placeholder ?? 'Select'"
        @update:model-value="$emit('update:modelValue', $event)"
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
        @update:model-value="$emit('update:modelValue', $event)"
      />
      <UiInput
        v-else
        :id="id"
        :model-value="modelValue"
        :type="inputType"
        :aria-describedby="describedBy"
        :invalid="invalid"
        :placeholder="field.placeholder ?? undefined"
        @update:model-value="$emit('update:modelValue', $event)"
      />
    </template>
  </UiFormField>
</template>
