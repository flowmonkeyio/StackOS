<script setup lang="ts">
import { computed, reactive } from 'vue'

import {
  UiButton,
  UiCallout,
  UiFormField,
  UiInput,
  UiLoadingState,
  UiSectionHeader,
  UiSelect,
  UiTextarea,
} from '@/components/ui'
import type { UiSelectOption } from '@/components/ui/UiSelect.vue'
import { useAgentDefaults, type AgentRole } from './useAgentDefaults'

const props = defineProps<{ projectId: number }>()
const {
  draft,
  hosts,
  tracker,
  loading,
  saving,
  error,
  message,
  dirty,
  ready,
  canSave,
  load,
  setField,
  save,
} = useAgentDefaults(computed(() => props.projectId))
const roles: Array<{ key: AgentRole; label: string }> = [
  { key: 'delivery_agent', label: 'Delivery agent' },
  { key: 'review_agent', label: 'Review agent' },
]
const custom = reactive<Record<AgentRole, boolean>>({ delivery_agent: false, review_agent: false })
const executableHosts = computed(() =>
  hosts.value.filter((host) => host.agent_execution?.sandboxes.length),
)
const empty = computed(() => Object.keys(draft.value).length === 0)

function value(role: AgentRole, field: string): string {
  return String(draft.value[role]?.[field] ?? '')
}

function hostKey(role: AgentRole): string {
  return value(role, 'host_key') || draft.value.host_key || ''
}

function host(role: AgentRole) {
  return hosts.value.find((item) => item.host_key === hostKey(role))
}

function hostOptions(role: AgentRole): UiSelectOption[] {
  const options: UiSelectOption[] = [
    {
      value: '',
      label: draft.value.host_key
        ? `Project harness (${draft.value.host_key})`
        : 'No project harness selected',
    },
    ...executableHosts.value.map((item) => ({
      value: item.host_key,
      label: item.display_name ?? item.host_key,
    })),
  ]
  const current = value(role, 'host_key')
  if (current && !executableHosts.value.some((item) => item.host_key === current)) {
    options.push({ value: current, label: `${current} (saved; unavailable in catalog)` })
  }
  return options
}

function modelOptions(role: AgentRole): UiSelectOption[] {
  const capability = host(role)?.agent_execution
  const current = value(role, 'model')
  const options: UiSelectOption[] = [
    {
      value: '',
      label: capability?.requires_provider_model
        ? 'Explicit model required for execution'
        : 'Native default',
      disabled: capability?.requires_provider_model,
    },
    ...(capability?.models ?? []).map((model) => ({ value: model, label: model })),
  ]
  if (current && !capability?.models.includes(current))
    options.push({ value: current, label: `${current} (saved/custom)` })
  if (allowsCustomModel(role)) options.push({ value: '__custom__', label: 'Custom model…' })
  return options
}

function allowsCustomModel(role: AgentRole): boolean {
  const capability = host(role)?.agent_execution
  return Boolean(
    capability?.custom_models || (capability?.provider_selection && value(role, 'provider')),
  )
}

function selectModel(role: AgentRole, selection: string | number | null) {
  custom[role] = selection === '__custom__'
  if (!custom[role]) setField(role, 'model', selection)
}
</script>

<template>
  <section
    class="space-y-5"
    aria-label="Project agent defaults"
  >
    <p class="text-sm text-fg-muted">
      Save instructions and native execution choices for future task handoffs. Existing task
      snapshots stay frozen. Saving does not start delivery. Installation and connection status do
      not prove authentication or model access.
    </p>
    <UiCallout
      v-if="error"
      tone="danger"
      title="Agent defaults unavailable"
    >
      {{ error }}
    </UiCallout>
    <UiCallout
      v-if="message"
      tone="success"
      title="Defaults saved"
    >
      {{ message }}
    </UiCallout>
    <UiLoadingState
      v-if="loading"
      label="Loading agent defaults…"
    />
    <form
      v-if="ready"
      class="space-y-6"
      @submit.prevent="save"
    >
      <p
        v-if="empty"
        class="text-sm text-fg-muted"
      >
        No project agent defaults saved. Set up the roles below for future handoffs.
      </p>
      <div class="grid items-start gap-6 xl:grid-cols-2">
        <section
          v-for="role in roles"
          :key="role.key"
          class="min-w-0 space-y-4"
          :aria-label="role.label"
        >
          <UiSectionHeader
            :title="role.label"
            as="h2"
          />
          <UiFormField
            label="Name"
            :input-id="`${role.key}-name`"
          >
            <UiInput
              :model-value="value(role.key, 'name')"
              :disabled="saving"
              @update:model-value="setField(role.key, 'name', $event)"
            />
          </UiFormField>
          <UiFormField
            label="Instructions"
            :input-id="`${role.key}-instructions`"
            help="Full project role instructions. Review the complete text before saving."
          >
            <UiTextarea
              :model-value="value(role.key, 'instructions')"
              :rows="10"
              :disabled="saving"
              @update:model-value="setField(role.key, 'instructions', $event)"
            />
          </UiFormField>
          <div class="grid gap-4 sm:grid-cols-2">
            <UiFormField
              label="Harness"
              :input-id="`${role.key}-host`"
            >
              <UiSelect
                :model-value="value(role.key, 'host_key')"
                :options="hostOptions(role.key)"
                :disabled="saving"
                @update:model-value="setField(role.key, 'host_key', $event)"
              />
            </UiFormField>
            <UiFormField
              label="Model"
              :input-id="`${role.key}-model`"
            >
              <UiSelect
                :model-value="custom[role.key] ? '__custom__' : value(role.key, 'model')"
                :options="modelOptions(role.key)"
                :disabled="saving || !host(role.key)?.agent_execution"
                @update:model-value="selectModel(role.key, $event)"
              />
            </UiFormField>
          </div>
          <UiFormField
            v-if="custom[role.key] && allowsCustomModel(role.key)"
            label="Custom model"
            :input-id="`${role.key}-custom-model`"
          >
            <UiInput
              :model-value="value(role.key, 'model')"
              :disabled="saving"
              @update:model-value="setField(role.key, 'model', $event)"
            />
          </UiFormField>
          <UiFormField
            v-if="
              host(role.key)?.agent_execution?.provider_selection || value(role.key, 'provider')
            "
            label="Provider"
            :input-id="`${role.key}-provider`"
            :help="
              host(role.key)?.agent_execution?.requires_provider_model
                ? 'Explicit provider and model required for execution.'
                : 'Optional native provider selector. Leave blank for the native default.'
            "
          >
            <UiInput
              :model-value="value(role.key, 'provider')"
              :disabled="saving || !host(role.key)?.agent_execution?.provider_selection"
              @update:model-value="setField(role.key, 'provider', $event)"
            />
          </UiFormField>
          <UiFormField
            v-if="host(role.key)?.agent_execution?.profile_selection || value(role.key, 'profile')"
            label="Profile"
            :input-id="`${role.key}-profile`"
            help="Optional native execution profile. Leave blank for the native default."
          >
            <UiInput
              :model-value="value(role.key, 'profile')"
              :disabled="saving || !host(role.key)?.agent_execution?.profile_selection"
              @update:model-value="setField(role.key, 'profile', $event)"
            />
          </UiFormField>
          <p
            v-if="host(role.key)"
            class="text-xs text-fg-muted"
          >
            {{ host(role.key)?.status_label ?? host(role.key)?.status }}.
            {{ host(role.key)?.message }}
            {{ host(role.key)?.agent_execution?.notes }}
          </p>
        </section>
      </div>
      <div class="flex items-center gap-3 border-t border-subtle pt-4">
        <UiButton
          type="submit"
          variant="primary"
          :disabled="!canSave"
          :loading="saving"
        >
          Save defaults
        </UiButton>
        <span class="text-xs text-fg-muted">{{
          dirty ? 'Unsaved changes' : `Tracker revision ${tracker?.rev}`
        }}</span>
      </div>
    </form>
    <UiButton
      variant="secondary"
      :disabled="loading || saving"
      @click="load"
    >
      {{
        dirty ? 'Discard changes and reload' : 'Reload defaults'
      }}
    </UiButton>
  </section>
</template>
