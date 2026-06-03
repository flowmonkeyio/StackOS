<script setup lang="ts">
import type { SchemaAuthProviderOut } from '@/api'
import {
  UiBadge,
  UiButton,
  UiCallout,
  UiFormField,
  UiInput,
  UiSecretInput,
  UiSelect,
  UiSidePanel,
} from '@/components/ui'

import { formatAuthType, pluginLabel, providerSetupNote, providerActionKey } from './formatters'
import type { AuthField, AuthMethod, MessageMap } from './types'

defineProps<{
  modelValue: boolean
  selectedProvider: SchemaAuthProviderOut | null
  visibleAuthProviders: SchemaAuthProviderOut[]
  providerOptions: Array<{ value: string; label: string; group?: string }>
  providerMessages: MessageMap
  busyAction: string | null
  authMethods: (provider: SchemaAuthProviderOut) => AuthMethod[]
  selectedMethodKey: (provider: SchemaAuthProviderOut) => string
  selectedMethod: (provider: SchemaAuthProviderOut) => AuthMethod | null
  supportsCredential: (provider: SchemaAuthProviderOut) => boolean
  inputType: (field: AuthField) => 'text' | 'url' | 'number' | 'email'
  isSecretField: (field: AuthField) => boolean
  primaryCredentialFields: (
    provider: SchemaAuthProviderOut,
    method: AuthMethod | null | undefined,
  ) => AuthField[]
  advancedCredentialFields: (
    provider: SchemaAuthProviderOut,
    method: AuthMethod | null | undefined,
  ) => AuthField[]
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
}>()
</script>

<template>
  <UiSidePanel
    :model-value="modelValue"
    title="Add connection"
    description="Choose a service and store the credential in the local daemon."
    size="lg"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <div v-if="selectedProvider" class="grid gap-4">
      <UiCallout v-if="visibleAuthProviders.length === 0" tone="info">
        Enable a plugin before adding provider connections.
      </UiCallout>

      <UiFormField label="Service">
        <template #default="{ id, describedBy, invalid }">
          <UiSelect
            :id="id"
            :model-value="selectedProvider.key"
            :options="providerOptions"
            :aria-describedby="describedBy"
            :invalid="invalid"
            searchable
            search-placeholder="Search services"
            empty-label="No services found"
            @update:model-value="$emit('select-provider', $event)"
          />
        </template>
      </UiFormField>

      <div class="rounded-md border border-subtle bg-bg-surface-alt p-3">
        <div class="flex flex-wrap items-center gap-2">
          <h3 class="text-sm font-semibold text-fg-strong">{{ selectedProvider.name }}</h3>
          <UiBadge tone="accent">{{ pluginLabel(selectedProvider.plugin_slug) }}</UiBadge>
          <UiBadge>{{ formatAuthType(selectedProvider.auth_type) }}</UiBadge>
        </div>
        <p v-if="selectedProvider.description" class="mt-1 text-sm text-fg-muted">
          {{ selectedProvider.description }}
        </p>
      </div>

      <UiCallout v-if="providerSetupNote(selectedProvider)" tone="info">
        {{ providerSetupNote(selectedProvider) }}
      </UiCallout>

      <template v-if="supportsCredential(selectedProvider) && selectedMethod(selectedProvider)">
        <UiFormField v-if="authMethods(selectedProvider).length > 1" label="Auth method">
          <template #default="{ id, describedBy, invalid }">
            <UiSelect
              :id="id"
              :model-value="selectedMethodKey(selectedProvider)"
              :options="
                authMethods(selectedProvider).map((method) => ({
                  value: method.key,
                  label: method.label,
                }))
              "
              :aria-describedby="describedBy"
              :invalid="invalid"
              @update:model-value="$emit('select-method', selectedProvider.key, $event)"
            />
          </template>
        </UiFormField>

        <UiFormField
          label="Connection name"
          help="Leave blank for the default account. Use a short name like client-a or sandbox when this service has more than one account."
        >
          <template #default="{ id, describedBy, invalid }">
            <UiInput
              :id="id"
              :model-value="
                profileValue(selectedProvider.key, selectedMethod(selectedProvider)?.key ?? '')
              "
              :aria-describedby="describedBy"
              :invalid="invalid"
              placeholder="default"
              @update:model-value="
                setProfileValue(
                  selectedProvider.key,
                  selectedMethod(selectedProvider)?.key ?? '',
                  $event,
                )
              "
            />
          </template>
        </UiFormField>

        <UiFormField label="Display label" help="Shown to operators and agents as safe metadata.">
          <template #default="{ id, describedBy, invalid }">
            <UiInput
              :id="id"
              :model-value="
                labelValue(selectedProvider.key, selectedMethod(selectedProvider)?.key ?? '')
              "
              :aria-describedby="describedBy"
              :invalid="invalid"
              placeholder="Primary account"
              @update:model-value="
                setLabelValue(
                  selectedProvider.key,
                  selectedMethod(selectedProvider)?.key ?? '',
                  $event,
                )
              "
            />
          </template>
        </UiFormField>

        <UiFormField
          v-for="field in primaryCredentialFields(
            selectedProvider,
            selectedMethod(selectedProvider),
          )"
          :key="field.key"
          :label="field.label"
          :help="field.description ?? undefined"
          :required="field.required"
        >
          <template #default="{ id, describedBy, invalid }">
            <UiSelect
              v-if="hasFieldOptions(field)"
              :id="id"
              :model-value="
                fieldValue(
                  selectedProvider.key,
                  selectedMethod(selectedProvider)?.key ?? '',
                  field.key,
                )
              "
              :options="fieldOptions(field)"
              :aria-describedby="describedBy"
              :invalid="invalid"
              :placeholder="field.placeholder ?? 'Select'"
              @update:model-value="
                setFieldValue(
                  selectedProvider.key,
                  selectedMethod(selectedProvider)?.key ?? '',
                  field.key,
                  $event,
                )
              "
            />
            <UiSecretInput
              v-else-if="isSecretField(field)"
              :id="id"
              :model-value="
                fieldValue(
                  selectedProvider.key,
                  selectedMethod(selectedProvider)?.key ?? '',
                  field.key,
                )
              "
              :aria-describedby="describedBy"
              :invalid="invalid"
              no-copy
              no-reveal
              :placeholder="field.placeholder ?? ''"
              @update:model-value="
                setFieldValue(
                  selectedProvider.key,
                  selectedMethod(selectedProvider)?.key ?? '',
                  field.key,
                  $event,
                )
              "
            />
            <UiInput
              v-else
              :id="id"
              :model-value="
                fieldValue(
                  selectedProvider.key,
                  selectedMethod(selectedProvider)?.key ?? '',
                  field.key,
                )
              "
              :type="inputType(field)"
              :aria-describedby="describedBy"
              :invalid="invalid"
              :placeholder="field.placeholder ?? undefined"
              @update:model-value="
                setFieldValue(
                  selectedProvider.key,
                  selectedMethod(selectedProvider)?.key ?? '',
                  field.key,
                  $event,
                )
              "
            />
          </template>
        </UiFormField>

        <details
          v-if="
            advancedCredentialFields(selectedProvider, selectedMethod(selectedProvider)).length > 0
          "
          class="rounded-md border border-subtle bg-bg-surface-alt"
        >
          <summary class="cursor-pointer px-3 py-2 text-sm font-medium text-fg-default focus-ring">
            Advanced connection settings
            <span class="ml-2 text-xs font-normal text-fg-muted">
              self-hosted Bot API and webhook secret overrides
            </span>
          </summary>
          <div class="grid gap-4 border-t border-subtle p-3">
            <UiFormField
              v-for="field in advancedCredentialFields(
                selectedProvider,
                selectedMethod(selectedProvider),
              )"
              :key="field.key"
              :label="field.label"
              :help="field.description ?? undefined"
              :required="field.required"
            >
              <template #default="{ id, describedBy, invalid }">
                <UiSelect
                  v-if="hasFieldOptions(field)"
                  :id="id"
                  :model-value="
                    fieldValue(
                      selectedProvider.key,
                      selectedMethod(selectedProvider)?.key ?? '',
                      field.key,
                    )
                  "
                  :options="fieldOptions(field)"
                  :aria-describedby="describedBy"
                  :invalid="invalid"
                  :placeholder="field.placeholder ?? 'Select'"
                  @update:model-value="
                    setFieldValue(
                      selectedProvider.key,
                      selectedMethod(selectedProvider)?.key ?? '',
                      field.key,
                      $event,
                    )
                  "
                />
                <UiSecretInput
                  v-else-if="isSecretField(field)"
                  :id="id"
                  :model-value="
                    fieldValue(
                      selectedProvider.key,
                      selectedMethod(selectedProvider)?.key ?? '',
                      field.key,
                    )
                  "
                  :aria-describedby="describedBy"
                  :invalid="invalid"
                  no-copy
                  no-reveal
                  :placeholder="field.placeholder ?? ''"
                  @update:model-value="
                    setFieldValue(
                      selectedProvider.key,
                      selectedMethod(selectedProvider)?.key ?? '',
                      field.key,
                      $event,
                    )
                  "
                />
                <UiInput
                  v-else
                  :id="id"
                  :model-value="
                    fieldValue(
                      selectedProvider.key,
                      selectedMethod(selectedProvider)?.key ?? '',
                      field.key,
                    )
                  "
                  :type="inputType(field)"
                  :aria-describedby="describedBy"
                  :invalid="invalid"
                  :placeholder="field.placeholder ?? undefined"
                  @update:model-value="
                    setFieldValue(
                      selectedProvider.key,
                      selectedMethod(selectedProvider)?.key ?? '',
                      field.key,
                      $event,
                    )
                  "
                />
              </template>
            </UiFormField>
          </div>
        </details>

        <UiCallout v-if="selectedMethod(selectedProvider)?.description" tone="info">
          {{ selectedMethod(selectedProvider)?.description }}
        </UiCallout>

        <UiCallout
          v-if="providerMessages[selectedProvider.key]"
          :tone="providerMessages[selectedProvider.key].tone"
        >
          {{ providerMessages[selectedProvider.key].text }}
        </UiCallout>
      </template>

      <UiCallout v-else tone="info"> No credential required. </UiCallout>
    </div>

    <UiCallout v-else tone="info"> Enable a plugin before adding provider connections. </UiCallout>

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
        :loading="busyAction === providerActionKey(selectedProvider.key, 'save')"
        :disabled="selectedMethod(selectedProvider)?.payload_format === 'none'"
        @click="$emit('save-credential', selectedProvider)"
      >
        Save connection
      </UiButton>
    </template>
  </UiSidePanel>
</template>
