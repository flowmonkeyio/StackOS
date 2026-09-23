<script setup lang="ts">
import { computed, ref } from 'vue'

import type { SchemaAccountAuthStatusOut, SchemaAuthProviderOut } from '@/api'
import { UiButton, UiCallout, UiSidePanel } from '@/components/ui'
import type { TelegramAccountSessionStatus } from '@/stores/plugins'

import { providerActionKey } from '@/views/connections/formatters'
import ConnectionCredentialFields from '@/views/connections/ConnectionCredentialFields.vue'
import ConnectionProviderSetupGuidance from '@/views/connections/ConnectionProviderSetupGuidance.vue'
import ConnectionServiceSelect from '@/views/connections/ConnectionServiceSelect.vue'
import type { AuthField, AuthMethod, MessageMap } from '@/views/connections/types'
import AccountSetupFlow, { type AccountSetupStep } from './AccountSetupFlow.vue'
import NativeAccountAuthorizationPanel from './NativeAccountAuthorizationPanel.vue'
import NativeAccountSessionPanel from './NativeAccountSessionPanel.vue'
import TelegramApplicationSetupSection from './TelegramApplicationSetupSection.vue'
import TelegramProxySetupSection from './TelegramProxySetupSection.vue'

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
  nativeAuthorizationActive: boolean
  nativeAuthorizationState: SchemaAccountAuthStatusOut | null
  nativeAuthorizationMode: 'phone' | 'qr'
  nativeAuthorizationAllowsQr: boolean
  nativeAuthorizationBusy: boolean
  nativeSessionActive: boolean
  nativeSessionState: TelegramAccountSessionStatus | null
  nativeSessionBusy: boolean
  nativeAccountDraftDirty: boolean
  telegramApplicationConfigured: boolean | null
  telegramApplicationBusy: boolean
  telegramApplicationError: string | null
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
  (e: 'save-account', provider: SchemaAuthProviderOut, onNativeSaved?: () => void): void
  (e: 'save-and-continue', provider: SchemaAuthProviderOut): void
  (e: 'update:native-authorization-mode', value: 'phone' | 'qr'): void
  (e: 'start-native-authorization', mode: 'phone' | 'qr'): void
  (
    e: 'submit-native-authorization',
    value: { generation: number; answer: Record<string, string> },
  ): void
  (e: 'cancel-native-authorization', generation: number): void
  (e: 'refresh-native-authorization'): void
  (e: 'connect-native-session'): void
  (e: 'disconnect-native-session'): void
  (e: 'refresh-native-session'): void
  (e: 'go-plugins'): void
  (e: 'refresh-telegram-application'): void
}>()

const showAccountDetails = ref(false)
const currentMethod = computed(() =>
  props.selectedProvider ? props.selectedMethod(props.selectedProvider) : null,
)
const isTelegram = computed(() => props.selectedProvider?.key === 'telegram')
const isTelegramUser = computed(
  () => isTelegram.value && currentMethod.value?.config?.account_kind === 'user',
)
const isTelegramBot = computed(
  () => isTelegram.value && currentMethod.value?.config?.account_kind === 'bot',
)
const isTelegramNative = computed(() => isTelegramUser.value || isTelegramBot.value)
const telegramStage = computed<'details' | 'signin' | 'ready'>(() => {
  if (!isTelegramNative.value || !props.nativeAuthorizationActive || showAccountDetails.value)
    return 'details'
  return ['connected', 'disconnected'].includes(props.nativeAuthorizationState?.status ?? '')
    ? 'ready'
    : 'signin'
})
const setupSteps = computed<AccountSetupStep[]>(() => {
  if (!isTelegramNative.value) return []
  return [
    {
      key: 'details',
      label: 'Account details',
      state: telegramStage.value === 'details' ? 'current' : 'complete',
    },
    {
      key: 'signin',
      label: isTelegramBot.value ? 'Verify bot' : 'Sign in',
      state:
        telegramStage.value === 'signin'
          ? 'current'
          : telegramStage.value === 'ready'
            ? 'complete'
            : 'upcoming',
    },
    {
      key: 'ready',
      label: 'Ready',
      state: telegramStage.value === 'ready' ? 'current' : 'upcoming',
    },
  ]
})
const accountFields = computed(() =>
  props
    .methodFields(currentMethod.value)
    .filter(
      (field) =>
        !isTelegram.value ||
        (!field.key.startsWith('proxy_') && !['api_id', 'api_hash'].includes(field.key)),
    ),
)
const proxyFields = computed(() =>
  isTelegram.value
    ? props.methodFields(currentMethod.value).filter((field) => field.key.startsWith('proxy_'))
    : [],
)

function setPanelOpen(open: boolean): void {
  if (!open) showAccountDetails.value = false
  emit('update:modelValue', open)
}

function selectProvider(value: string | number | null): void {
  showAccountDetails.value = false
  emit('select-provider', value)
}

function selectMethod(providerKey: string, value: string | number | null): void {
  showAccountDetails.value = false
  emit('select-method', providerKey, value)
}

function accountMethodDescription(method: AuthMethod): string {
  if (method.config?.account_kind === 'user')
    return 'Sign in with a phone number and the challenge Telegram requests.'
  if (method.config?.account_kind === 'bot')
    return 'Use the token issued by BotFather for this bot.'
  return method.description ?? ''
}

function submitAccount(): void {
  const provider = props.selectedProvider
  const method = provider ? props.selectedMethod(provider) : null
  if (!provider || !method) return
  if (method.interactive) emit('start-provider', provider)
  else if (isTelegramUser.value && !props.editing) emit('save-and-continue', provider)
  else
    emit('save-account', provider, () => {
      showAccountDetails.value = false
    })
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
    :title="
      isTelegramNative
        ? editing
          ? 'Edit Telegram Account'
          : isTelegramBot
            ? 'Set up Telegram bot'
            : 'Connect Telegram'
        : editing
          ? 'Edit Account'
          : 'Add Account'
    "
    :description="
      isTelegramNative
        ? isTelegramBot
          ? telegramApplicationConfigured === false && !editing
            ? 'Set up the TDLib application once, then enter this bot’s BotFather token.'
            : 'Enter this bot’s BotFather token. We verify it when you save.'
          : telegramApplicationConfigured === false && !editing
            ? 'Set up the TDLib application once, then complete Telegram sign-in in this drawer.'
            : 'Save the Account, then complete Telegram sign-in in this drawer.'
        : editing
          ? 'Update this reusable Account without exposing its stored secret.'
          : 'Create a reusable provider Account. You can attach it to any project.'
    "
    size="lg"
    @update:model-value="setPanelOpen"
  >
    <AccountSetupFlow :steps="setupSteps" aria-label="Telegram Account setup">
      <form
        v-if="!isTelegramNative || telegramStage === 'details'"
        id="account-credential-form"
        @submit.prevent="submitAccount"
      >
        <p v-if="!isTelegramNative" class="mb-4 text-xs leading-5 text-fg-muted">
          Credentials stay in the local daemon. Connected agents receive only safe references.
        </p>
        <UiButton
          v-else-if="nativeAuthorizationActive"
          class="mb-4"
          size="sm"
          variant="ghost"
          icon-left="arrow-left"
          :disabled="nativeAccountDraftDirty"
          @click="showAccountDetails = false"
        >
          Back to
          {{
            nativeAuthorizationState?.status === 'connected' ||
            nativeAuthorizationState?.status === 'disconnected'
              ? 'ready'
              : isTelegramBot
                ? 'verification'
                : 'sign-in'
          }}
        </UiButton>

        <div v-if="selectedProvider" class="grid gap-4">
          <UiCallout v-if="visibleAuthProviders.length === 0" tone="info">
            Enable a plugin before adding provider connections.
          </UiCallout>

          <ConnectionServiceSelect
            :selected-provider="selectedProvider"
            :providers="visibleAuthProviders"
            :options="providerOptions"
            :disabled="editing"
            @select="selectProvider"
          />

          <ConnectionProviderSetupGuidance
            v-if="!isTelegram"
            :provider="selectedProvider"
            :editing="editing"
          />

          <template v-if="supportsCredential(selectedProvider)">
            <ConnectionCredentialFields
              :auth-methods="authMethods(selectedProvider)"
              :selected-method-key="selectedMethodKey(selectedProvider)"
              :selected-method="currentMethod"
              :display-name-value="displayNameValue(selectedProvider.key, currentMethod?.key ?? '')"
              :fields="accountFields"
              :input-type="inputType"
              :is-secret-field="isSecretField"
              :has-field-options="hasFieldOptions"
              :field-options="fieldOptions"
              :field-values="credentialFieldValues(selectedProvider, currentMethod)"
              :field-errors="fieldErrors"
              :editing="editing"
              :secret-present="secretPresent"
              :method-description="isTelegram ? accountMethodDescription : undefined"
              :show-selected-method-description="!isTelegram"
              :show-editing-method-hint="!isTelegram"
              @select-method="selectMethod(selectedProvider.key, $event)"
              @update:display-name="
                setDisplayNameValue(selectedProvider.key, currentMethod?.key ?? '', $event)
              "
              @update:field="
                currentMethod && updateCredentialField(selectedProvider, currentMethod, $event)
              "
            >
              <template v-if="isTelegramNative && !editing" #before-account-fields>
                <UiCallout
                  v-if="telegramApplicationConfigured === null"
                  :tone="telegramApplicationError ? 'danger' : 'info'"
                  density="compact"
                >
                  {{ telegramApplicationError ?? 'Checking saved Telegram application setup…' }}
                  <template v-if="telegramApplicationError" #actions>
                    <UiButton
                      size="sm"
                      variant="secondary"
                      :loading="telegramApplicationBusy"
                      @click="$emit('refresh-telegram-application')"
                    >
                      Retry
                    </UiButton>
                  </template>
                </UiCallout>
                <TelegramApplicationSetupSection
                  v-else-if="!telegramApplicationConfigured && currentMethod"
                  :api-id="fieldValue(selectedProvider.key, currentMethod.key, 'api_id')"
                  :api-hash="fieldValue(selectedProvider.key, currentMethod.key, 'api_hash')"
                  :errors="fieldErrors"
                  @update:api-id="
                    setFieldValue(selectedProvider.key, currentMethod.key, 'api_id', $event)
                  "
                  @update:api-hash="
                    setFieldValue(selectedProvider.key, currentMethod.key, 'api_hash', $event)
                  "
                />
                <p v-if="isTelegramUser" class="text-xs leading-5 text-fg-muted">
                  Your phone number and sign-in challenge come after saving this Account.
                </p>
              </template>
            </ConnectionCredentialFields>

            <p v-if="isTelegram && editing" class="text-xs leading-5 text-fg-muted">
              This Account keeps its {{ currentMethod?.label ?? 'selected' }} sign-in method. Create
              another Account to switch between bot and user.
            </p>

            <TelegramProxySetupSection
              v-if="isTelegram && currentMethod"
              :key="`${selectedProvider.key}:${currentMethod.key}:${editing ? 'edit' : 'new'}`"
              :fields="proxyFields"
              :input-type="inputType"
              :is-secret-field="isSecretField"
              :has-field-options="hasFieldOptions"
              :field-options="fieldOptions"
              :field-values="credentialFieldValues(selectedProvider, currentMethod)"
              :field-errors="fieldErrors"
              :editing="editing"
              :secret-present="secretPresent"
              @update:field="updateCredentialField(selectedProvider, currentMethod, $event)"
            />

            <UiCallout
              v-if="providerMessages[selectedProvider.key]"
              :tone="providerMessages[selectedProvider.key].tone"
              density="compact"
            >
              {{ providerMessages[selectedProvider.key].text }}
            </UiCallout>

            <UiCallout
              v-if="isTelegramUser && nativeSessionState?.desired_connected"
              tone="warning"
              density="compact"
            >
              Disconnect the Telegram session before changing application or proxy settings. An
              Account name change can be saved while connected.
            </UiCallout>

            <UiCallout
              v-else-if="
                nativeAccountDraftDirty && (nativeAuthorizationActive || nativeSessionActive)
              "
              tone="warning"
              density="compact"
            >
              Save or discard Account changes before connecting or continuing Telegram
              authorization.
            </UiCallout>

            <NativeAccountSessionPanel
              v-if="nativeSessionActive && !isTelegramNative"
              :state="nativeSessionState"
              :busy="nativeSessionBusy"
              :dirty="nativeAccountDraftDirty"
              @connect="$emit('connect-native-session')"
              @disconnect="$emit('disconnect-native-session')"
              @refresh="$emit('refresh-native-session')"
            />
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

      <div v-else class="grid gap-4">
        <div class="flex items-start justify-between gap-3">
          <p class="text-xs leading-5 text-fg-muted">
            {{
              telegramStage === 'ready'
                ? 'Your authorization is saved on this device.'
                : isTelegramBot
                  ? 'Retry verification with the saved bot token.'
                  : 'Complete the challenge Telegram requests.'
            }}
          </p>
          <UiButton size="sm" variant="ghost" @click="showAccountDetails = true">
            Edit details
          </UiButton>
        </div>
        <UiCallout
          v-if="selectedProvider && providerMessages[selectedProvider.key]"
          :tone="providerMessages[selectedProvider.key].tone"
          density="compact"
        >
          {{ providerMessages[selectedProvider.key].text }}
        </UiCallout>
        <NativeAccountAuthorizationPanel
          :key="`${nativeAuthorizationState?.credential_ref ?? 'pending'}:${nativeAuthorizationState?.challenge?.generation ?? nativeAuthorizationState?.generation ?? 'none'}:${nativeAuthorizationState?.challenge?.kind ?? nativeAuthorizationState?.status ?? 'none'}`"
          :state="nativeAuthorizationState"
          :account-kind="isTelegramBot ? 'bot' : 'user'"
          :mode="nativeAuthorizationMode"
          :allows-qr="nativeAuthorizationAllowsQr"
          :busy="nativeAuthorizationBusy"
          :session-available="nativeSessionActive"
          :dirty="nativeAccountDraftDirty"
          @update:mode="$emit('update:native-authorization-mode', $event)"
          @start="$emit('start-native-authorization', $event)"
          @submit="$emit('submit-native-authorization', $event)"
          @cancel="$emit('cancel-native-authorization', $event)"
          @refresh="$emit('refresh-native-authorization')"
        />
        <NativeAccountSessionPanel
          v-if="telegramStage === 'ready' && nativeSessionActive"
          :state="nativeSessionState"
          :busy="nativeSessionBusy"
          :dirty="nativeAccountDraftDirty"
          @connect="$emit('connect-native-session')"
          @disconnect="$emit('disconnect-native-session')"
          @refresh="$emit('refresh-native-session')"
        />
      </div>
    </AccountSetupFlow>

    <template #footer>
      <UiButton
        v-if="isTelegramNative && telegramStage === 'ready'"
        variant="primary"
        @click="setPanelOpen(false)"
      >
        Done
      </UiButton>
      <UiButton v-else variant="ghost" @click="setPanelOpen(false)">
        {{ isTelegramNative && telegramStage === 'signin' ? 'Close' : 'Cancel' }}
      </UiButton>
      <UiButton
        v-if="isTelegramNative && telegramStage === 'details'"
        variant="primary"
        type="submit"
        form="account-credential-form"
        :loading="
          Boolean(
            selectedProvider && busyAction === providerActionKey(selectedProvider.key, 'save'),
          )
        "
        :disabled="!currentMethod || (isTelegram && !editing && telegramApplicationConfigured === null)"
        @click.prevent="submitAccount"
      >
        {{ editing ? 'Save changes' : isTelegramBot ? 'Save and verify' : 'Save and continue' }}
      </UiButton>
      <UiButton
        v-else-if="
          selectedProvider &&
          selectedMethod(selectedProvider)?.interactive &&
          !nativeAuthorizationActive &&
          !isTelegramNative
        "
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
        v-else-if="selectedProvider && !isTelegramNative && (editing || !nativeAuthorizationActive)"
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
        {{
          nativeAuthorizationAllowsQr ||
          selectedMethod(selectedProvider)?.config?.native_authorization
            ? editing
              ? 'Save changes'
              : 'Save Account'
            : editing
              ? 'Save changes'
              : 'Save and verify'
        }}
      </UiButton>
    </template>
  </UiSidePanel>
</template>
