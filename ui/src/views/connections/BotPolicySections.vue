<script setup lang="ts">
import { UiFormField, UiInput, UiTextarea } from '@/components/ui'

import type { BotPolicyFieldKey, BotPolicyFormFields } from './types'

defineProps<{
  form: BotPolicyFormFields
  purposePlaceholder: string
}>()

const emit = defineEmits<{
  (e: 'update-field', key: BotPolicyFieldKey, value: string | number | null): void
}>()
</script>

<template>
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
          @update:model-value="emit('update-field', 'identity_display_name', $event)"
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
          :placeholder="purposePlaceholder"
          @update:model-value="emit('update-field', 'identity_purpose', $event)"
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
          @update:model-value="emit('update-field', 'identity_voice', $event)"
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
          @update:model-value="emit('update-field', 'agent_default_instructions', $event)"
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
            @update:model-value="emit('update-field', 'agent_boundaries', $event)"
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
            @update:model-value="emit('update-field', 'agent_escalation', $event)"
          />
        </template>
      </UiFormField>
    </div>
  </section>
</template>
