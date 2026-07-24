<script setup lang="ts">
import { computed, useId } from 'vue'

import { UiCallout, UiRadioGroup } from '@/components/ui'

import ConnectionCredentialField from './ConnectionCredentialField.vue'
import ConnectionMetadataFields from './ConnectionMetadataFields.vue'
import type { AuthField, AuthMethod } from './types'

const props = defineProps<{
  authMethods: AuthMethod[]
  selectedMethodKey: string
  selectedMethod: AuthMethod | null
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

const authMethodLegendId = useId()

const authMethodOptions = computed(() =>
  props.authMethods.map((method) => ({
    value: method.key,
    label: method.label,
    description: methodChoiceDescription(method),
  })),
)

function methodChoiceDescription(method: AuthMethod): string {
  return [
    `Description: ${method.description || 'No additional provider description is available.'}`,
    `Audience: ${methodAudience(method)}`,
    `Lifecycle: ${methodLifecycle(method)}`,
    `Permission verification: ${permissionVerificationGuidance(method)}`,
  ].join(' ')
}

function methodAudience(method: AuthMethod): string {
  return method.interactive
    ? 'Operators who can authorize a provider account in this browser.'
    : 'Operators who hold a credential for the account they want to connect.'
}

function methodLifecycle(method: AuthMethod): string {
  return method.interactive
    ? 'StackOS saves this profile, then starts the provider authorization flow.'
    : 'StackOS saves this profile locally, then tests the credential.'
}

function permissionVerificationGuidance(method: AuthMethod): string {
  const posture = method.permission_verification
  if (!posture) return 'This provider has not declared an additional verification posture.'
  if (posture.enforcement === 'provider_enforced') {
    return 'The provider enforces permissions; StackOS cannot verify them locally.'
  }
  if (posture.evidence_source === 'unavailable') {
    return 'StackOS cannot verify permissions locally, so actions that require verified permissions stay blocked.'
  }
  if (posture.evidence_source === 'oauth_response') {
    return 'StackOS records permission evidence returned during authorization.'
  }
  return 'StackOS verifies permission evidence when you test this connection.'
}
</script>

<template>
  <fieldset
    v-if="authMethods.length > 1"
    class="m-0 flex min-w-0 flex-col gap-1.5 border-0 p-0"
  >
    <legend
      :id="authMethodLegendId"
      class="text-xs font-medium text-fg-default"
    >
      Authentication method
      <span class="text-danger" aria-hidden="true">*</span>
    </legend>
    <UiRadioGroup
      name="connection-auth-method"
      variant="card"
      :model-value="selectedMethodKey || null"
      :options="authMethodOptions"
      :disabled="editing"
      :aria-labelledby="authMethodLegendId"
      @update:model-value="$emit('select-method', $event)"
    />
  </fieldset>

  <UiCallout
    v-if="authMethods.length > 1 && !selectedMethod"
    tone="info"
    density="compact"
  >
    Choose an authentication method to review its setup and continue.
  </UiCallout>

  <template v-if="selectedMethod">
    <UiCallout
      v-if="editing"
      tone="info"
      density="compact"
    >
      Authentication method is locked to {{ selectedMethod.label }}. To change methods, create a
      separate named profile, test it, explicitly reassign exact consumers to the new credential
      reference, then locally revoke the old profile when it is no longer used.
    </UiCallout>

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

    <UiCallout
      v-if="selectedMethod.description"
      tone="info"
      density="compact"
    >
      {{ selectedMethod.description }}
    </UiCallout>
  </template>
</template>
