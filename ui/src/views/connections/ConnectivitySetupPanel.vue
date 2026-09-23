<script setup lang="ts">
import { UiButton, UiCallout, UiFormField, UiInput, UiRadioGroup, UiSidePanel } from '@/components/ui'

import type { IngressForm, MessageTone } from './types'

const props = defineProps<{
  modelValue: boolean
  form: IngressForm
  busyAction: string | null
  message: { tone: MessageTone; text: string } | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'update:form', value: IngressForm): void
  (e: 'save'): void
}>()

function patch(part: Partial<IngressForm>): void {
  emit('update:form', { ...props.form, ...part })
}

const driverOptions = [
  {
    value: 'public-url',
    label: 'Public URL',
    description: 'A deployed HTTPS address that configured webhook providers can reach directly.',
  },
  {
    value: 'local-tunnel',
    label: 'Local tunnel',
    description: 'Auto-discover a development tunnel (e.g. ngrok) running on this machine.',
  },
]
</script>

<template>
  <UiSidePanel
    :model-value="modelValue"
    title="Set up connectivity"
    description="Tell StackOS the public address webhook providers should use to deliver messages to your bots. Telegram bot and user updates run through each Account's TDLib session."
    size="md"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="grid gap-5">
      <UiCallout
        v-if="message"
        :tone="message.tone"
      >
        {{ message.text }}
      </UiCallout>

      <UiFormField label="How should providers reach your bots?">
        <template #default="{ describedBy }">
          <UiRadioGroup
            name="ingress-driver"
            variant="card"
            :model-value="form.driver"
            :options="driverOptions"
            :aria-describedby="describedBy"
            @update:model-value="patch({ driver: $event as IngressForm['driver'] })"
          />
        </template>
      </UiFormField>

      <UiFormField
        v-if="form.driver === 'public-url'"
        label="Public address"
        help="The base HTTPS URL of this StackOS instance. Each bot gets its own path underneath it."
        required
      >
        <template #default="{ id, describedBy, invalid }">
          <UiInput
            :id="id"
            :model-value="form.public_base_url"
            :aria-describedby="describedBy"
            :invalid="invalid"
            type="url"
            placeholder="https://stackos.example.com"
            @update:model-value="patch({ public_base_url: String($event ?? '') })"
          />
        </template>
      </UiFormField>

      <UiFormField
        v-else
        label="Tunnel discovery URL"
        help="The local API your tunnel exposes so StackOS can read its current public address (ngrok default shown)."
      >
        <template #default="{ id, describedBy, invalid }">
          <UiInput
            :id="id"
            :model-value="form.discovery_url"
            :aria-describedby="describedBy"
            :invalid="invalid"
            type="url"
            placeholder="http://127.0.0.1:4040/api/endpoints"
            @update:model-value="patch({ discovery_url: String($event ?? '') })"
          />
        </template>
      </UiFormField>

      <UiCallout
        tone="info"
        density="compact"
      >
        After saving, use <strong>Refresh routes</strong> to get each required provider URL. Copy
        any manual setup URL into that provider’s console. Signing secrets stay daemon-side.
      </UiCallout>
    </div>

    <template #footer>
      <UiButton
        variant="ghost"
        @click="emit('update:modelValue', false)"
      >
        Cancel
      </UiButton>
      <UiButton
        variant="primary"
        icon-left="save"
        :loading="busyAction === 'ingress:configure'"
        @click="emit('save')"
      >
        Save
      </UiButton>
    </template>
  </UiSidePanel>
</template>
