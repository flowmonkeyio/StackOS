<script setup lang="ts">
import {
  UiButton,
  UiCallout,
  UiCheckbox,
  UiFormField,
  UiInput,
  UiSelect,
  UiSidePanel,
  UiTextarea,
} from '@/components/ui'

import BotPolicySections from './BotPolicySections.vue'
import { botUsernameFromConnection, telegramConnectionForProfile } from './formatters'
import type {
  BotPolicyFieldKey,
  ConnectionRow,
  MessageTone,
  TelegramCommandDraft,
  TelegramProfileForm,
} from './types'

const props = defineProps<{
  modelValue: boolean
  form: TelegramProfileForm
  telegramConnectionOptions: Array<{ value: string; label: string }>
  telegramConnections: ConnectionRow[]
  message: { tone: MessageTone; text: string } | null
  busyAction: string | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'update:form', value: TelegramProfileForm): void
  (e: 'save'): void
  (e: 'add-command'): void
  (e: 'remove-command', index: number): void
}>()

function updateForm(patch: Partial<TelegramProfileForm>): void {
  emit('update:form', { ...props.form, ...patch })
}

function updateTextField(key: keyof TelegramProfileForm, value: string | number | null): void {
  updateForm({ [key]: String(value ?? '') } as Partial<TelegramProfileForm>)
}

function updateBooleanField(key: keyof TelegramProfileForm, value: boolean): void {
  updateForm({ [key]: value } as Partial<TelegramProfileForm>)
}

function updatePolicyField(key: BotPolicyFieldKey, value: string | number | null): void {
  updateTextField(key, value)
}

function updateCommandText(
  index: number,
  key: keyof TelegramCommandDraft,
  value: string | number | null,
): void {
  const commands = props.form.commands.map((command, commandIndex) =>
    commandIndex === index ? { ...command, [key]: String(value ?? '') } : command,
  )
  updateForm({ commands })
}

function updateCommandEnabled(index: number, value: boolean): void {
  const commands = props.form.commands.map((command, commandIndex) =>
    commandIndex === index ? { ...command, enabled: value } : command,
  )
  updateForm({ commands })
}
</script>

<template>
  <UiSidePanel
    :model-value="modelValue"
    title="Telegram profile"
    description="Configure static bot policy. Secrets stay in the selected connection."
    size="lg"
    @update:model-value="$emit('update:modelValue', $event)"
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
          label="Profile key"
          help="Project-scoped key used by webhook paths and agent-readable setup."
          required
        >
          <template #default="{ id, describedBy, invalid }">
            <UiInput
              :id="id"
              :model-value="form.key"
              :aria-describedby="describedBy"
              :invalid="invalid"
              placeholder="ops-bot"
              @update:model-value="updateTextField('key', $event)"
            />
          </template>
        </UiFormField>

        <UiFormField
          label="Telegram connection"
          help="Only the profile key is exposed here; the token stays daemon-side."
          required
        >
          <template #default="{ id, describedBy, invalid }">
            <UiSelect
              :id="id"
              :model-value="form.auth_profile_key"
              :options="telegramConnectionOptions"
              :aria-describedby="describedBy"
              :invalid="invalid"
              placeholder="Select connection"
              @update:model-value="updateTextField('auth_profile_key', $event)"
            />
          </template>
        </UiFormField>

        <UiCallout
          v-if="form.auth_profile_key"
          tone="info"
          density="compact"
        >
          Telegram identity:
          {{
            botUsernameFromConnection(
              telegramConnectionForProfile(form.auth_profile_key, telegramConnections),
            )
              ? `@${botUsernameFromConnection(
                telegramConnectionForProfile(form.auth_profile_key, telegramConnections),
              )}`
              : 'test the selected connection to fetch it from Telegram'
          }}
        </UiCallout>
      </section>

      <BotPolicySections
        :form="form"
        purpose-placeholder="Handle approved operational requests from Telegram."
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
            label="Visible chats"
            help="Optional comma-separated StackOS refs. Leave blank to let the bot observe any chat it has access to; only allowlisted users can trigger replies."
          >
            <template #default="{ id, describedBy, invalid }">
              <UiInput
                :id="id"
                :model-value="form.allowed_chat_refs"
                :aria-describedby="describedBy"
                :invalid="invalid"
                placeholder="telegram-chat:999"
                @update:model-value="updateTextField('allowed_chat_refs', $event)"
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
                placeholder="telegram-user:555"
                @update:model-value="updateTextField('allowed_user_refs', $event)"
              />
            </template>
          </UiFormField>
        </div>

        <UiFormField label="Mentions">
          <template #default="{ id, describedBy, invalid }">
            <UiInput
              :id="id"
              :model-value="form.mention_patterns"
              :aria-describedby="describedBy"
              :invalid="invalid"
              placeholder="ops, urgent"
              @update:model-value="updateTextField('mention_patterns', $event)"
            />
          </template>
        </UiFormField>
      </section>

      <section
        class="grid gap-3 border-t border-subtle pt-4"
        aria-label="Command intents"
      >
        <div class="flex items-start justify-between gap-3">
          <div>
            <h3 class="t-h3 text-fg-strong">
              Command intents
            </h3>
            <p class="mt-0.5 text-xs text-fg-muted">
              Optional triggers with guidance passed to the operating agent.
            </p>
          </div>
          <UiButton
            size="sm"
            variant="secondary"
            icon-left="plus"
            @click="$emit('add-command')"
          >
            Add command
          </UiButton>
        </div>

        <div
          v-for="(command, index) in form.commands"
          :key="index"
          class="grid gap-3 rounded-lg border border-subtle bg-bg-surface-alt p-3"
        >
          <div class="grid gap-3 sm:grid-cols-[minmax(8rem,12rem)_1fr_auto] sm:items-end">
            <UiFormField label="Command">
              <template #default="{ id, describedBy, invalid }">
                <UiInput
                  :id="id"
                  :model-value="command.command"
                  :aria-describedby="describedBy"
                  :invalid="invalid"
                  placeholder="/ops"
                  @update:model-value="updateCommandText(index, 'command', $event)"
                />
              </template>
            </UiFormField>

            <UiFormField label="Description">
              <template #default="{ id, describedBy, invalid }">
                <UiInput
                  :id="id"
                  :model-value="command.description"
                  :aria-describedby="describedBy"
                  :invalid="invalid"
                  placeholder="Handle approved operational requests"
                  @update:model-value="updateCommandText(index, 'description', $event)"
                />
              </template>
            </UiFormField>

            <UiButton
              size="sm"
              variant="danger-ghost"
              icon-left="trash"
              @click="$emit('remove-command', index)"
            >
              Remove
            </UiButton>
          </div>

          <UiFormField label="Command guidance">
            <template #default="{ id, describedBy, invalid }">
              <UiTextarea
                :id="id"
                :model-value="command.guidance"
                :aria-describedby="describedBy"
                :invalid="invalid"
                :rows="3"
                placeholder="Explain what the agent should gather, decide, and return for this command."
                @update:model-value="updateCommandText(index, 'guidance', $event)"
              />
            </template>
          </UiFormField>

          <UiCheckbox
            :model-value="command.enabled"
            label="Command enabled"
            @update:model-value="updateCommandEnabled(index, $event)"
          />
        </div>
      </section>

      <details class="rounded-lg border border-subtle bg-bg-surface-alt">
        <summary
          class="focus-ring cursor-pointer rounded-lg px-3 py-2 text-sm font-medium text-fg-default"
        >
          Advanced delivery behavior
        </summary>
        <div class="grid gap-3 border-t border-subtle p-3">
          <UiCheckbox
            :model-value="form.store_non_trigger_messages"
            label="Store non-trigger messages"
            @update:model-value="updateBooleanField('store_non_trigger_messages', $event)"
          />
          <UiCheckbox
            :model-value="form.origin_required"
            label="Require origin-bound replies"
            @update:model-value="updateBooleanField('origin_required', $event)"
          />
          <UiCheckbox
            :model-value="form.reply_to_source_message"
            label="Reply to source message"
            @update:model-value="updateBooleanField('reply_to_source_message', $event)"
          />
          <UiCheckbox
            :model-value="form.same_thread"
            label="Use same thread when available"
            @update:model-value="updateBooleanField('same_thread', $event)"
          />
        </div>
      </details>
    </div>

    <template #footer>
      <UiButton
        variant="ghost"
        @click="$emit('update:modelValue', false)"
      >
        Cancel
      </UiButton>
      <UiButton
        variant="primary"
        icon-left="save"
        :loading="busyAction === 'telegram-profile:save'"
        @click="$emit('save')"
      >
        Save Telegram profile
      </UiButton>
    </template>
  </UiSidePanel>
</template>
