<script setup lang="ts">
import type { SchemaAuthProviderOut } from '@/api'
import { UiButton, UiCallout, UiSidePanel } from '@/components/ui'

import { providerSetupNote, providerActionKey } from './formatters'
import ConnectionCredentialFields from './ConnectionCredentialFields.vue'
import ConnectionServiceSelect from './ConnectionServiceSelect.vue'
import type { AuthField, AuthMethod, MessageMap } from './types'

const props = defineProps<{
  modelValue: boolean
  selectedProvider: SchemaAuthProviderOut | null
  visibleAuthProviders: SchemaAuthProviderOut[]
  providerOptions: Array<{ value: string; label: string; group?: string }>
  providerMessages: MessageMap
  providerSetupUrls: Record<string, string>
  fieldErrors: Record<string, string>
  busyAction: string | null
  editing: boolean
  secretPresent: Record<string, boolean>
  authMethods: (provider: SchemaAuthProviderOut) => AuthMethod[]
  selectedMethodKey: (provider: SchemaAuthProviderOut) => string
  selectedMethod: (provider: SchemaAuthProviderOut) => AuthMethod | null
  supportsCredential: (provider: SchemaAuthProviderOut) => boolean
  inputType: (field: AuthField) => 'text' | 'url' | 'number' | 'email'
  isSecretField: (field: AuthField) => boolean
  methodFields: (method: AuthMethod | null | undefined) => AuthField[]
  hasFieldOptions: (field: AuthField) => boolean
  fieldOptions: (field: AuthField) => Array<{ value: string; label: string }>
  profileValue: (providerKey: string, methodKey: string) => string
  setProfileValue: (providerKey: string, methodKey: string, value: string | number | null) => void
  labelValue: (providerKey: string, methodKey: string) => string
  setLabelValue: (providerKey: string, methodKey: string, value: string | number | null) => void
  fieldValue: (providerKey: string, methodKey: string, fieldKey: string) => string
  setFieldValue: (
    providerKey: string,
    methodKey: string,
    fieldKey: string,
    value: string | number | null,
  ) => void
}>()

defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'select-provider', value: string | number | null): void
  (e: 'select-method', providerKey: string, value: string | number | null): void
  (e: 'start-provider', provider: SchemaAuthProviderOut): void
  (e: 'save-credential', provider: SchemaAuthProviderOut): void
  (e: 'go-plugins'): void
}>()

function credentialFieldValues(provider: SchemaAuthProviderOut, method: AuthMethod) {
  return Object.fromEntries(
    (method.fields ?? []).map((field) => [
      field.key,
      props.fieldValue(provider.key, method.key, field.key),
    ]),
  )
}

function updateCredentialField(
  provider: SchemaAuthProviderOut,
  method: AuthMethod,
  update: { fieldKey: string; value: string | number | null },
) {
  props.setFieldValue(provider.key, method.key, update.fieldKey, update.value)
}
</script>

<template>
  <UiSidePanel
    :model-value="modelValue"
    :title="editing ? 'Edit connection' : 'Add connection'"
    :description="
      editing
        ? 'Update connection settings without exposing the stored secret.'
        : 'Choose a service and store the credential in the local daemon.'
    "
    size="lg"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <form
      id="connection-credential-form"
      @submit.prevent="selectedProvider && $emit('save-credential', selectedProvider)"
    >
      <p class="mb-4 text-xs leading-5 text-fg-muted">
        Credentials stay in the local daemon. Connected agents receive only safe references.
      </p>

      <div v-if="selectedProvider" class="grid gap-4">
        <UiCallout v-if="visibleAuthProviders.length === 0" tone="info">
          Enable a plugin before adding provider connections.
        </UiCallout>

        <ConnectionServiceSelect
          :selected-provider="selectedProvider"
          :providers="visibleAuthProviders"
          :options="providerOptions"
          :disabled="editing"
          @select="$emit('select-provider', $event)"
        />

        <UiCallout v-if="providerSetupNote(selectedProvider)" tone="info" density="compact">
          {{ providerSetupNote(selectedProvider) }}
        </UiCallout>

        <template v-if="supportsCredential(selectedProvider) && selectedMethod(selectedProvider)">
          <ConnectionCredentialFields
            :auth-methods="authMethods(selectedProvider)"
            :selected-method-key="selectedMethodKey(selectedProvider)"
            :selected-method="selectedMethod(selectedProvider)!"
            :profile-value="
              profileValue(selectedProvider.key, selectedMethod(selectedProvider)?.key ?? '')
            "
            :label-value="
              labelValue(selectedProvider.key, selectedMethod(selectedProvider)?.key ?? '')
            "
            :fields="methodFields(selectedMethod(selectedProvider))"
            :input-type="inputType"
            :is-secret-field="isSecretField"
            :has-field-options="hasFieldOptions"
            :field-options="fieldOptions"
            :field-values="
              credentialFieldValues(selectedProvider, selectedMethod(selectedProvider)!)
            "
            :field-errors="fieldErrors"
            :editing="editing"
            :secret-present="secretPresent"
            @select-method="$emit('select-method', selectedProvider.key, $event)"
            @update:profile="
              setProfileValue(
                selectedProvider.key,
                selectedMethod(selectedProvider)?.key ?? '',
                $event,
              )
            "
            @update:label="
              setLabelValue(
                selectedProvider.key,
                selectedMethod(selectedProvider)?.key ?? '',
                $event,
              )
            "
            @update:field="
              updateCredentialField(selectedProvider, selectedMethod(selectedProvider)!, $event)
            "
          />

          <UiCallout
            v-if="providerMessages[selectedProvider.key]"
            :tone="providerMessages[selectedProvider.key].tone"
          >
            {{ providerMessages[selectedProvider.key].text }}
            <template v-if="providerSetupUrls[selectedProvider.key]" #actions>
              <a
                class="focus-ring rounded-sm font-medium text-fg-link hover:underline"
                :href="providerSetupUrls[selectedProvider.key]"
                target="_blank"
                rel="noopener noreferrer"
              >
                Continue setup →
              </a>
            </template>
          </UiCallout>
        </template>

        <UiCallout v-else tone="info"> No credential required. </UiCallout>
      </div>

      <UiCallout v-else tone="info">
        Enable a plugin before adding provider connections.
        <template #actions>
          <UiButton size="sm" variant="secondary" icon-left="puzzle" @click="$emit('go-plugins')">
            Go to Plugins
          </UiButton>
        </template>
      </UiCallout>
    </form>

    <template #footer>
      <UiButton variant="ghost" @click="$emit('update:modelValue', false)"> Cancel </UiButton>
      <UiButton
        v-if="selectedProvider && selectedMethod(selectedProvider)?.interactive"
        variant="secondary"
        icon-left="external-link"
        :loading="busyAction === providerActionKey(selectedProvider.key, 'start')"
        @click="$emit('start-provider', selectedProvider)"
      >
        Start setup
      </UiButton>
      <UiButton
        v-if="selectedProvider"
        variant="primary"
        icon-left="save"
        type="submit"
        form="connection-credential-form"
        :loading="busyAction === providerActionKey(selectedProvider.key, 'save')"
        :disabled="selectedMethod(selectedProvider)?.payload_format === 'none'"
        @click.prevent="$emit('save-credential', selectedProvider)"
      >
        {{ editing ? 'Save changes' : 'Save and verify' }}
      </UiButton>
    </template>
  </UiSidePanel>
</template>
