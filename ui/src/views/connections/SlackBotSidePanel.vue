<script setup lang="ts">
import {
  UiButton,
  UiCallout,
  UiFormField,
  UiInput,
  UiSelect,
  UiSidePanel,
  UiTextarea,
} from '@/components/ui'

import type { MessageTone, SlackProfileForm } from './types'

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
              :model-value="form.auth_profile_key"
              :options="slackConnectionOptions"
              :aria-describedby="describedBy"
              :invalid="invalid"
              placeholder="Select connection"
              @update:model-value="updateField('auth_profile_key', $event)"
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
      </section>

      <section
        class="grid gap-4 border-t border-subtle pt-4"
        aria-label="Identity"
      >
        <h3 class="t-h3 text-fg-strong">
          Identity
        </h3>

        <UiFormField
          label="Display name"
          required
        >
          <template #default="{ id, describedBy, invalid }">
            <UiInput
              :id="id"
              :model-value="form.identity_display_name"
              :aria-describedby="describedBy"
              :invalid="invalid"
              placeholder="Ops Bot"
              @update:model-value="updateField('identity_display_name', $event)"
            />
          </template>
        </UiFormField>

        <UiFormField label="Purpose">
          <template #default="{ id, describedBy, invalid }">
            <UiTextarea
              :id="id"
              :model-value="form.identity_purpose"
              :aria-describedby="describedBy"
              :invalid="invalid"
              :rows="3"
              placeholder="Handle approved Slack requests for the team."
              @update:model-value="updateField('identity_purpose', $event)"
            />
          </template>
        </UiFormField>

        <UiFormField label="Voice">
          <template #default="{ id, describedBy, invalid }">
            <UiTextarea
              :id="id"
              :model-value="form.identity_voice"
              :aria-describedby="describedBy"
              :invalid="invalid"
              :rows="2"
              placeholder="Clear, concise, and operational."
              @update:model-value="updateField('identity_voice', $event)"
            />
          </template>
        </UiFormField>
      </section>

      <section
        class="grid gap-4 border-t border-subtle pt-4"
        aria-label="Agent guidance"
      >
        <h3 class="t-h3 text-fg-strong">
          Agent guidance
        </h3>

        <UiFormField
          label="Agent instructions"
          help="Static guidance attached to every agent request created by this bot."
        >
          <template #default="{ id, describedBy, invalid }">
            <UiTextarea
              :id="id"
              :model-value="form.agent_default_instructions"
              :aria-describedby="describedBy"
              :invalid="invalid"
              :rows="4"
              placeholder="Triage the request, inspect relevant project context, and reply only when the next action is clear."
              @update:model-value="updateField('agent_default_instructions', $event)"
            />
          </template>
        </UiFormField>

        <div class="grid gap-4 sm:grid-cols-2">
          <UiFormField label="Boundaries">
            <template #default="{ id, describedBy, invalid }">
              <UiTextarea
                :id="id"
                :model-value="form.agent_boundaries"
                :aria-describedby="describedBy"
                :invalid="invalid"
                :rows="3"
                placeholder="Do not change accounts, spend budget, or promise outcomes without explicit approval."
                @update:model-value="updateField('agent_boundaries', $event)"
              />
            </template>
          </UiFormField>

          <UiFormField label="Escalation">
            <template #default="{ id, describedBy, invalid }">
              <UiTextarea
                :id="id"
                :model-value="form.agent_escalation"
                :aria-describedby="describedBy"
                :invalid="invalid"
                :rows="3"
                placeholder="Escalate billing, legal, or destructive actions before executing."
                @update:model-value="updateField('agent_escalation', $event)"
              />
            </template>
          </UiFormField>
        </div>
      </section>

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
