<script setup lang="ts">
import { UiCallout, UiFormField, UiSelect } from '@/components/ui'

import ConnectionCredentialField from './ConnectionCredentialField.vue'
import ConnectionMetadataFields from './ConnectionMetadataFields.vue'
import type { AuthField, AuthMethod } from './types'

defineProps<{
  authMethods: AuthMethod[]
  selectedMethodKey: string
  selectedMethod: AuthMethod
  profileValue: string
  labelValue: string
  fields: AuthField[]
  inputType: (field: AuthField) => 'text' | 'url' | 'number' | 'email'
  isSecretField: (field: AuthField) => boolean
  hasFieldOptions: (field: AuthField) => boolean
  fieldOptions: (field: AuthField) => Array<{ value: string; label: string }>
  fieldValues: Record<string, string>
  fieldErrors: Record<string, string>
  editing: boolean
  secretPresent: Record<string, boolean>
}>()

defineEmits<{
  (event: 'select-method', value: string | number | null): void
  (event: 'update:profile', value: string | number | null): void
  (event: 'update:label', value: string | number | null): void
  (event: 'update:field', update: { fieldKey: string; value: string | number | null }): void
}>()
</script>

<template>
  <UiFormField v-if="authMethods.length > 1" label="Auth method">
    <template #default="{ id, describedBy, invalid }">
      <UiSelect
        :id="id"
        :model-value="selectedMethodKey"
        :options="
          authMethods.map((method) => ({
            value: method.key,
            label: method.label,
          }))
        "
        :aria-describedby="describedBy"
        :invalid="invalid"
        :disabled="editing"
        @update:model-value="$emit('select-method', $event)"
      />
    </template>
  </UiFormField>

  <ConnectionMetadataFields
    :profile-value="profileValue"
    :label-value="labelValue"
    :profile-readonly="editing"
    @update:profile="$emit('update:profile', $event)"
    @update:label="$emit('update:label', $event)"
  />

  <ConnectionCredentialField
    v-for="field in fields"
    :key="field.key"
    :field="field"
    :model-value="fieldValues[field.key] ?? ''"
    :input-type="inputType(field)"
    :secret="isSecretField(field)"
    :select="hasFieldOptions(field)"
    :options="fieldOptions(field)"
    :error="fieldErrors[field.key]"
    :editing="editing"
    :secret-present="secretPresent[field.key] ?? false"
    @update:model-value="$emit('update:field', { fieldKey: field.key, value: $event })"
  />

  <UiCallout v-if="selectedMethod.description" tone="info" density="compact">
    {{ selectedMethod.description }}
  </UiCallout>
</template>
