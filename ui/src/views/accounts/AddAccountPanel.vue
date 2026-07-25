<script setup lang="ts">
import type { SchemaAuthProviderOut } from '@/api'
import { UiButton, UiCallout, UiSidePanel } from '@/components/ui'

import { providerActionKey } from '@/views/connections/formatters'
import ConnectionCredentialFields from '@/views/connections/ConnectionCredentialFields.vue'
import ConnectionProviderSetupGuidance from '@/views/connections/ConnectionProviderSetupGuidance.vue'
import ConnectionServiceSelect from '@/views/connections/ConnectionServiceSelect.vue'
import type { AuthField, AuthMethod, MessageMap } from '@/views/connections/types'

const props = defineProps<{
  modelValue: boolean
  selectedProvider: SchemaAuthProviderOut | null
  visibleAuthProviders: SchemaAuthProviderOut[]
  providerOptions: Array<{ value: string; label: string; group?: string }>
  providerMessages: MessageMap
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
  displayNameValue: (providerKey: string, methodKey: string) => string
  setDisplayNameValue: (
    providerKey: string,
    methodKey: string,
    value: string | number | null,
  ) => void
  fieldValue: (providerKey: string, methodKey: string, fieldKey: string) => string
  setFieldValue: (
    providerKey: string,
    methodKey: string,
    fieldKey: string,
    value: string | number | null,
  ) => void
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'select-provider', value: string | number | null): void
  (e: 'select-method', providerKey: string, value: string | number | null): void
  (e: 'start-provider', provider: SchemaAuthProviderOut): void
  (e: 'save-account', provider: SchemaAuthProviderOut): void
  (e: 'go-plugins'): void
}>()

function submitAccount(): void {
  const provider = props.selectedProvider
  const method = provider ? props.selectedMethod(provider) : null
  if (!provider || !method) return
  if (method.interactive) emit('start-provider', provider)
  else emit('save-account', provider)
}

function credentialFieldValues(provider: SchemaAuthProviderOut, method: AuthMethod | null) {
  if (!method) return {}
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
    :title="editing ? 'Edit Account' : 'Add Account'"
    :description="
      editing
        ? 'Update this reusable Account without exposing its stored secret.'
        : 'Create a reusable provider Account. You can attach it to any project.'
    "
    size="lg"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <form id="account-credential-form" @submit.prevent="submitAccount">
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

        <ConnectionProviderSetupGuidance :provider="selectedProvider" :editing="editing" />

        <template v-if="supportsCredential(selectedProvider)">
          <ConnectionCredentialFields
            :auth-methods="authMethods(selectedProvider)"
            :selected-method-key="selectedMethodKey(selectedProvider)"
            :selected-method="selectedMethod(selectedProvider)"
            :display-name-value="
              displayNameValue(
                selectedProvider.key,
                selectedMethod(selectedProvider)?.key ?? '',
              )
            "
            :fields="methodFields(selectedMethod(selectedProvider))"
            :input-type="inputType"
            :is-secret-field="isSecretField"
            :has-field-options="hasFieldOptions"
            :field-options="fieldOptions"
            :field-values="
              credentialFieldValues(selectedProvider, selectedMethod(selectedProvider))
            "
            :field-errors="fieldErrors"
            :editing="editing"
            :secret-present="secretPresent"
            @select-method="$emit('select-method', selectedProvider.key, $event)"
            @update:display-name="
              setDisplayNameValue(
                selectedProvider.key,
                selectedMethod(selectedProvider)?.key ?? '',
                $event,
              )
            "
            @update:field="
              selectedMethod(selectedProvider) &&
              updateCredentialField(selectedProvider, selectedMethod(selectedProvider)!, $event)
            "
          />

          <UiCallout
            v-if="providerMessages[selectedProvider.key]"
            :tone="providerMessages[selectedProvider.key].tone"
          >
            {{ providerMessages[selectedProvider.key].text }}
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
        variant="primary"
        icon-left="external-link"
        type="submit"
        form="account-credential-form"
        :loading="busyAction === providerActionKey(selectedProvider.key, 'start')"
        @click.prevent="submitAccount"
      >
        {{ editing ? 'Reconnect' : 'Connect' }}
      </UiButton>
      <UiButton
        v-else-if="selectedProvider"
        variant="primary"
        icon-left="save"
        type="submit"
        form="account-credential-form"
        :loading="busyAction === providerActionKey(selectedProvider.key, 'save')"
        :disabled="
          !selectedMethod(selectedProvider) ||
          selectedMethod(selectedProvider)?.payload_format === 'none'
        "
        @click.prevent="submitAccount"
      >
        {{ editing ? 'Save changes' : 'Save and verify' }}
      </UiButton>
    </template>
  </UiSidePanel>
</template>
