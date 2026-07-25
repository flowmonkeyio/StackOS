<script setup lang="ts">
import {
  UiButton,
  UiCallout,
  UiCheckbox,
  UiFormField,
  UiInput,
  UiSelect,
  UiSidePanel,
} from '@/components/ui'

import BotPolicySections from './BotPolicySections.vue'
import type { BotPolicyFieldKey, MessageTone, SlackProfileForm } from './types'

const props = defineProps<{
  modelValue: boolean
  form: SlackProfileForm
  isNew: boolean
  slackConnectionOptions: Array<{ value: string; label: string }>
  teamLabel: string
  message: { tone: MessageTone; text: string } | null
  busyAction: string | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'update:form', value: SlackProfileForm): void
  (e: 'save'): void
}>()

function updateField(key: keyof SlackProfileForm, value: string | number | null): void {
  emit('update:form', { ...props.form, [key]: String(value ?? '') })
}

function updatePolicyField(key: BotPolicyFieldKey, value: string | number | null): void {
  updateField(key, value)
}

function updateBooleanField(key: keyof SlackProfileForm, value: boolean): void {
  emit('update:form', { ...props.form, [key]: value })
}
</script>

<template>
  <UiSidePanel
    :model-value="modelValue"
    title="Slack bot"
    description="Configure static bot policy. The token and signing secret stay in the selected connection."
    size="lg"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="grid gap-5">
      <UiCallout
        v-if="message"
        :tone="message.tone"
      >
        {{ message.text }}
      </UiCallout>

      <section
        class="grid gap-4"
        aria-label="Connection"
      >
        <UiFormField
          label="Bot key"
          help="Project-scoped key used by webhook paths and agent-readable setup."
          :required="isNew"
        >
          <template #default="{ id, describedBy, invalid }">
            <UiInput
              :id="id"
              :model-value="form.key"
              :aria-describedby="describedBy"
              :invalid="invalid"
              :disabled="!isNew"
              placeholder="ops-bot"
              @update:model-value="updateField('key', $event)"
            />
          </template>
        </UiFormField>

        <UiFormField
          label="Slack connection"
          help="Only the connection key is exposed here; the token and signing secret stay daemon-side."
          required
        >
          <template #default="{ id, describedBy, invalid }">
            <UiSelect
              :id="id"
              :model-value="form.credential_ref"
              :options="slackConnectionOptions"
              :aria-describedby="describedBy"
              :invalid="invalid"
              placeholder="Select connection"
              @update:model-value="updateField('credential_ref', $event)"
            />
          </template>
        </UiFormField>

        <UiCallout
          v-if="teamLabel"
          tone="info"
          density="compact"
        >
          Slack workspace: {{ teamLabel }}
        </UiCallout>

        <div class="rounded-lg border border-subtle bg-bg-surface-alt p-3">
          <UiCheckbox
            :model-value="form.ingress_enabled"
            label="Receive inbound Slack events in this project"
            description="A Slack app can send inbound events to one project. Turn this off when reusing the Account here only for outbound messages."
            @update:model-value="updateBooleanField('ingress_enabled', $event)"
          />
        </div>
      </section>

      <BotPolicySections
        :form="form"
        purpose-placeholder="Handle approved Slack requests for the team."
        @update-field="updatePolicyField"
      />

      <section
        class="grid gap-4 border-t border-subtle pt-4"
        aria-label="Access and triggers"
      >
        <h3 class="t-h3 text-fg-strong">
          Access and triggers
        </h3>

        <div class="grid gap-4 sm:grid-cols-2">
          <UiFormField
            label="Allowed channels"
            help="Optional comma-separated StackOS refs. Leave blank to let the bot observe any channel it’s in; only allowlisted users can trigger replies."
          >
            <template #default="{ id, describedBy, invalid }">
              <UiInput
                :id="id"
                :model-value="form.allowed_chat_refs"
                :aria-describedby="describedBy"
                :invalid="invalid"
                placeholder="slack-channel:C123"
                @update:model-value="updateField('allowed_chat_refs', $event)"
              />
            </template>
          </UiFormField>

          <UiFormField
            label="Allowed users"
            help="Comma-separated StackOS refs. Only these users can trigger work or replies."
            required
          >
            <template #default="{ id, describedBy, invalid }">
              <UiInput
                :id="id"
                :model-value="form.allowed_user_refs"
                :aria-describedby="describedBy"
                :invalid="invalid"
                placeholder="slack-user:U123"
                @update:model-value="updateField('allowed_user_refs', $event)"
              />
            </template>
          </UiFormField>
        </div>

        <UiFormField
          label="Mentions"
          help="Optional comma-separated mention patterns that trigger the bot in channels."
        >
          <template #default="{ id, describedBy, invalid }">
            <UiInput
              :id="id"
              :model-value="form.mention_patterns"
              :aria-describedby="describedBy"
              :invalid="invalid"
              placeholder="ops, urgent"
              @update:model-value="updateField('mention_patterns', $event)"
            />
          </template>
        </UiFormField>
      </section>
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
        :loading="busyAction === 'slack-profile:save'"
        @click="emit('save')"
      >
        Save Slack bot
      </UiButton>
    </template>
  </UiSidePanel>
</template>
