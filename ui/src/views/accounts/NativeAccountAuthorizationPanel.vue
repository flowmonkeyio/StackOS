<script setup lang="ts">
import { computed, ref } from 'vue'

import type { SchemaAccountAuthStatusOut } from '@/api'
import {
  UiButton,
  UiCallout,
  UiFormField,
  UiInput,
  UiRadioGroup,
  UiSecretInput,
} from '@/components/ui'

type AuthorizationMode = 'phone' | 'qr'

const props = withDefaults(
  defineProps<{
    state: SchemaAccountAuthStatusOut | null
    accountKind?: 'bot' | 'user'
    mode: AuthorizationMode
    allowsQr: boolean
    busy: boolean
    sessionAvailable?: boolean
    dirty?: boolean
  }>(),
  { accountKind: 'user', sessionAvailable: false, dirty: false },
)

const emit = defineEmits<{
  (event: 'update:mode', value: AuthorizationMode): void
  (event: 'start', mode: AuthorizationMode): void
  (event: 'submit', value: { generation: number; answer: Record<string, string> }): void
  (event: 'cancel', generation: number): void
  (event: 'refresh'): void
}>()

const answer = ref<{ challengeKey: string; values: Record<string, string> }>({
  challengeKey: '',
  values: {},
})
const challenge = computed(() => props.state?.challenge ?? null)
const challengeFields = computed(() => challenge.value?.fields ?? [])
const challengeKey = computed(() =>
  challenge.value
    ? `${props.state?.credential_ref}:${challenge.value.generation}:${challenge.value.kind}`
    : '',
)
const currentAnswer = computed(() =>
  answer.value.challengeKey === challengeKey.value ? answer.value.values : {},
)
const requiredFields = computed(() =>
  challengeFields.value.filter((field) => field !== 'last_name'),
)
const canSubmit = computed(
  () =>
    !props.dirty &&
    challenge.value !== null &&
    requiredFields.value.length > 0 &&
    requiredFields.value.every((field) => Boolean(currentAnswer.value[field]?.trim())),
)
const qrLink = computed(() => {
  const value = challenge.value?.qr_link
  return typeof value === 'string' && value.startsWith('tg://login?') ? value : null
})
const setupRequired = computed(
  () => props.state === null || ['pending', 'repair-required'].includes(props.state.status),
)
const waitingForNativeState = computed(() =>
  ['starting', 'verifying'].includes(props.state?.status ?? ''),
)
const cancelGeneration = computed(() => props.state?.generation ?? null)
const signInSaved = computed(() =>
  ['connected', 'disconnected'].includes(props.state?.status ?? ''),
)
const authorizationTitle = computed(() => {
  if (signInSaved.value)
    return props.accountKind === 'bot' ? 'Telegram bot authorization saved' : 'Telegram sign-in saved'
  if (props.accountKind === 'bot') return 'Verify Telegram bot'
  switch (challenge.value?.kind) {
    case 'phone_number':
      return 'Enter your phone number'
    case 'code':
      return 'Enter the Telegram code'
    case 'password':
      return 'Enter your two-step verification password'
    case 'qr':
      return 'Confirm sign-in in Telegram'
    case 'email_address':
      return 'Enter your email address'
    case 'email_code':
      return 'Enter the email code'
    case 'registration':
      return 'Finish Telegram registration'
    default:
      return 'Sign in to Telegram'
  }
})
const authorizationDescription = computed(() => {
  if (signInSaved.value) return ''
  if (props.accountKind === 'bot') return 'Telegram verifies the saved bot token and stores its authorization on this device.'
  switch (challenge.value?.kind) {
    case 'phone_number':
      return 'Use the international format, including the country code.'
    case 'code':
      return codeDeliveryHint.value
        ? `Telegram sent a sign-in code via ${codeDeliveryHint.value}.`
        : 'Enter the sign-in code Telegram sent to you.'
    case 'password':
      return 'Use your Telegram two-step verification password, not the Telegram code.'
    case 'qr':
      return 'Confirm this sign-in from Telegram on another device.'
    case 'email_address':
      return 'Telegram needs an email address to continue sign-in.'
    case 'email_code':
      return 'Enter the code Telegram sent to your email address.'
    case 'registration':
      return 'Enter a first name to finish creating your Telegram account.'
    default:
      return 'Follow Telegram’s sign-in prompts to save the session locally.'
  }
})
const codeDeliveryHint = computed(() => {
  const deliveryType = challenge.value?.metadata?.delivery_type
  return (
    {
      authenticationCodeTypeTelegramMessage: 'Telegram message',
      authenticationCodeTypeSms: 'SMS',
      authenticationCodeTypeCall: 'a phone call',
      authenticationCodeTypeFlashCall: 'a flash call',
      authenticationCodeTypeMissedCall: 'a missed call',
    }[typeof deliveryType === 'string' ? deliveryType : ''] ?? null
  )
})
const submitLabel = computed(() => {
  switch (challenge.value?.kind) {
    case 'phone_number':
      return 'Send Telegram code'
    case 'code':
      return 'Verify code'
    case 'password':
      return 'Sign in'
    case 'registration':
      return 'Finish registration'
    default:
      return 'Continue'
  }
})

function labelFor(field: string): string {
  if (field === 'code' && challenge.value?.kind === 'email_code') return 'Email code'
  return (
    {
      phone_number: 'Phone number',
      code: 'Telegram code',
      password: 'Password',
      email_address: 'Email address',
      first_name: 'First name',
      last_name: 'Last name',
    }[field] ?? field
  )
}

function inputTypeFor(field: string): 'text' | 'tel' | 'email' {
  if (field === 'phone_number') return 'tel'
  if (field === 'email_address') return 'email'
  return 'text'
}

function placeholderFor(field: string): string | undefined {
  if (field === 'phone_number') return '+15551234567'
  if (field === 'code') return 'Enter code'
  if (field === 'password') return 'Telegram password'
  return undefined
}

function setAnswer(field: string, value: string | number | null): void {
  answer.value = {
    challengeKey: challengeKey.value,
    values: { ...currentAnswer.value, [field]: String(value ?? '') },
  }
}

function submit(): void {
  const current = challenge.value
  if (props.dirty || !current || !canSubmit.value) return
  emit('submit', { generation: current.generation, answer: { ...currentAnswer.value } })
}

function startAuthorization(): void {
  if (props.dirty) return
  answer.value = { challengeKey: '', values: {} }
  emit('start', props.mode)
}

function cancelAuthorization(generation: number): void {
  answer.value = { challengeKey: '', values: {} }
  emit('cancel', generation)
}
</script>

<template>
  <section class="grid gap-4" aria-label="Native authorization">
    <div>
      <h3 class="text-sm font-semibold text-fg-strong">{{ authorizationTitle }}</h3>
      <p v-if="authorizationDescription" class="mt-1 text-xs leading-5 text-fg-muted">
        {{ authorizationDescription }}
      </p>
    </div>

    <UiCallout v-if="state?.repair_hint" tone="danger" density="compact">
      {{ state.repair_hint }}
    </UiCallout>

    <template v-if="setupRequired">
      <UiRadioGroup
        v-if="allowsQr"
        name="native-authorization-mode"
        variant="card"
        :model-value="mode"
        :options="[
          {
            value: 'phone',
            label: 'Phone number',
            description: 'Receive a Telegram sign-in challenge.',
          },
          {
            value: 'qr',
            label: 'QR code',
            description: 'Continue with Telegram on another device.',
          },
        ]"
        :disabled="busy"
        aria-label="Authorization method"
        @update:model-value="$emit('update:mode', $event as AuthorizationMode)"
      />
      <UiCallout v-else tone="info" density="compact">
        This Account uses its saved bot token to authorize with Telegram.
      </UiCallout>
      <UiButton
        class="justify-self-start"
        variant="primary"
        icon-left="play"
        :loading="busy"
        :disabled="busy || dirty"
        @click="startAuthorization"
      >
        {{ accountKind === 'bot' ? 'Retry bot verification' : 'Start authorization' }}
      </UiButton>
    </template>

    <template v-else-if="waitingForNativeState">
      <UiCallout tone="info" density="compact">
        {{
          state?.status === 'verifying'
            ? "Telegram is verifying this Account's native identity."
            : 'Telegram is starting this Account’s native session.'
        }}
        Refresh status shortly.
      </UiCallout>
      <div class="flex flex-wrap gap-2">
        <UiButton
          size="sm"
          variant="secondary"
          icon-left="refresh-cw"
          :loading="busy"
          @click="$emit('refresh')"
        >
          Refresh status
        </UiButton>
        <UiButton
          v-if="cancelGeneration !== null"
          size="sm"
          variant="ghost"
          aria-label="Cancel authorization"
          :disabled="busy"
          @click="cancelAuthorization(cancelGeneration)"
        >
          Cancel
        </UiButton>
      </div>
    </template>

    <template v-else-if="challenge?.kind === 'qr'">
      <UiCallout tone="info" density="compact" title="Confirm in Telegram">
        Approve this sign-in in Telegram on a signed-in device, then refresh the status here.
        <template #actions>
          <a
            v-if="qrLink"
            :href="qrLink"
            class="focus-ring rounded-sm text-xs font-medium text-fg-link underline underline-offset-2"
          >
            Open Telegram
          </a>
        </template>
      </UiCallout>
      <div class="flex flex-wrap gap-2">
        <UiButton
          size="sm"
          variant="secondary"
          icon-left="refresh-cw"
          :loading="busy"
          @click="$emit('refresh')"
        >
          Refresh status
        </UiButton>
        <UiButton
          size="sm"
          variant="ghost"
          aria-label="Cancel authorization"
          :disabled="busy"
          @click="cancelAuthorization(challenge.generation)"
        >
          Cancel
        </UiButton>
      </div>
    </template>

    <form v-else-if="challenge" class="grid gap-3" @submit.prevent="submit">
      <UiFormField
        v-for="field in challengeFields"
        :key="field"
        :label="labelFor(field)"
        :required="field !== 'last_name'"
        :show-optional="field === 'last_name'"
      >
        <UiSecretInput
          v-if="field === 'code' || field === 'password'"
          :model-value="currentAnswer[field] ?? ''"
          :aria-label="labelFor(field)"
          :placeholder="placeholderFor(field)"
          :no-copy="true"
          :no-reveal="true"
          required
          :disabled="busy"
          @update:model-value="setAnswer(field, $event)"
        />
        <UiInput
          v-else
          :model-value="currentAnswer[field] ?? ''"
          :type="inputTypeFor(field)"
          :placeholder="placeholderFor(field)"
          :aria-label="labelFor(field)"
          :autocomplete="
            field === 'phone_number' ? 'tel' : field === 'email_address' ? 'email' : 'off'
          "
          :required="field !== 'last_name'"
          :disabled="busy"
          @update:model-value="setAnswer(field, $event)"
        />
      </UiFormField>
      <div class="flex flex-wrap gap-2">
        <UiButton variant="primary" type="submit" :disabled="!canSubmit" :loading="busy">
          {{ submitLabel }}
        </UiButton>
        <UiButton
          variant="ghost"
          type="button"
          aria-label="Cancel authorization"
          :disabled="busy"
          @click="cancelAuthorization(challenge.generation)"
        >
          Cancel
        </UiButton>
      </div>
    </form>

    <UiCallout v-else-if="signInSaved" tone="success" density="compact">
      <template v-if="state?.status === 'connected'"> This Account is connected. </template>
      <template v-else-if="accountKind === 'bot' && sessionAvailable">
        Bot authorization is saved. Connect the session below when an agent needs this Account.
      </template>
      <template v-else-if="accountKind === 'bot'">
        Bot authorization is saved. Attach this Account to a project before connecting it for an agent.
      </template>
      <template v-else-if="sessionAvailable">
        Sign-in is saved. Connect the session below when an agent needs this Account.
      </template>
      <template v-else>
        Sign-in is saved. Attach this Account to a project, then connect it from that project's
        Connections page when an agent needs it.
      </template>
    </UiCallout>
  </section>
</template>
