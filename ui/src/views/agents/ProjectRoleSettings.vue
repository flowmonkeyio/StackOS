<script setup lang="ts">
import { computed, inject, onBeforeUnmount, ref, toRef } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, routerKey } from 'vue-router'
import { UiButton, UiCallout, UiConfirmDialog, UiFormField, UiInput, UiSelect, UiTextarea } from '@/components/ui'
import { executionAvailable, executionStatus, reasoningOptions, reconcileReasoning } from '@/components/domain/agentSelection'
import { isDesktopShell } from '@/lib/desktop'
import { useHomeAgentHostStatuses } from '@/views/home/useHomeAgentHostStatuses'
import { agentIdentity, type AgentSummary } from './useProjectAgents'
import { useRoleSettings, type HarnessRole } from './useRoleSettings'

const props = withDefaults(defineProps<{ projectId: number; projectDir: string; sources: AgentSummary[]; folderReady?: boolean }>(), { folderReady: true })
const opened = ref(false)
const settings = useRoleSettings(toRef(props, 'projectId'), toRef(props, 'projectDir'), toRef(props, 'folderReady'))
const { role, defaults, draft, effective, effectiveError, dirty, roleDirty, ready, scopeChanged, loading, saving, checking, error, message } = settings
const { hostStatuses, loadHostStatuses } = useHomeAgentHostStatuses(isDesktopShell())
const hostKey = computed(() => draft.value.host_key || defaults.value.host_key || (role.value === 'main_agent' ? effective.value.main_agent?.host_key : null) || 'codex')
const host = computed(() => hostStatuses.value.items.find(item => item.host_key === hostKey.value))
const catalog = computed(() => host.value?.agent_execution)
const hostOptions = computed(() => [
  { value: '', label: `Project / native default (${defaults.value.host_key || current.value?.host_key || 'codex'})` },
  ...hostStatuses.value.items.map(item => ({
    value: item.host_key, label: `${item.display_name || item.host_key}${executionStatus(item) ? ` · ${executionStatus(item)}` : ''}`,
    disabled: !executionAvailable(item) || !item.agent_execution?.sandboxes.includes('workspace-write'),
  })),
  ...(draft.value.host_key && !hostStatuses.value.items.some(item => item.host_key === draft.value.host_key)
    ? [{ value: draft.value.host_key, label: `${draft.value.host_key} (status unavailable)` }] : []),
])
const customModel = ref(false)
const modelOptions = computed(() => [
  { value: '', label: 'Native default (inherited)' },
  ...[...new Set([...(catalog.value?.models ?? []), ...(draft.value.model ? [draft.value.model] : [])])].map(value => ({ value, label: value })),
  ...(catalog.value?.custom_models ? [{ value: '__custom__', label: 'Custom model…' }] : []),
])
const efforts = computed(() => reasoningOptions(catalog.value, draft.value.model ?? null, draft.value.model_reasoning_effort ?? null, 'Native default (inherited)'))
const sourceOptions = computed(() => [
  { value: '', label: 'Choose a Codex project source' },
  ...props.sources.filter(item => item.source_ref.host_key === 'codex').map(item => ({ value: agentIdentity(item.source_ref), label: item.source_ref.native_name })),
])
const selectedSource = computed(() => draft.value.source_ref ? agentIdentity(draft.value.source_ref) : '')
const current = computed(() => props.folderReady && !scopeChanged.value ? effective.value[role.value] : undefined)
const effectiveText = computed(() => {
  const value = current.value
  if (!value) return ''
  const effort = value.native_settings?.model_reasoning_effort ?? value.model_reasoning_effort
  const origin = (key: string) => value.setting_sources?.[key] === 'source' ? 'Codex source' : value.setting_sources?.[key] === 'host' || !(key === 'model' ? value.model : effort) ? 'native default' : 'project default'
  return `${value.host_key} · Model: ${value.model || 'native default'} (${origin('model')}) · Reasoning: ${effort || 'native default'} (${origin('model_reasoning_effort')})`
})
async function open() { opened.value = true; await Promise.all([settings.load(), loadHostStatuses()]) }
function refresh() { if (opened.value) return settings.load() }
defineExpose({ saving, refresh })
function selectRole(value: string | number | null) { customModel.value = false; settings.selectRole(value as HarnessRole) }
function changeHost(value: string | number | null) { customModel.value = false; settings.changeHost(String(value ?? '')); }
function changeModel(value: string | number | null) {
  customModel.value = value === '__custom__'
  if (customModel.value) return
  const model = String(value ?? '') || null
  settings.set('model', model)
  settings.set('model_reasoning_effort', reconcileReasoning(catalog.value, model, draft.value.model_reasoning_effort ?? null))
}
function changeSource(value: string | number | null) {
  settings.set('source_ref', props.sources.find(item => agentIdentity(item.source_ref) === value)?.source_ref ?? null)
  settings.set('instructions', null)
}
const confirmLeave = ref(false)
let navigation: ((value: boolean) => void) | null = null
function finishNavigation(discard: boolean) { confirmLeave.value = false; navigation?.(discard); navigation = null }
if (inject(routerKey, null)) {
  const protect = () => saving.value ? false : !dirty.value ? true : new Promise<boolean>(resolve => { navigation?.(false); navigation = resolve; confirmLeave.value = true })
  onBeforeRouteLeave(protect); onBeforeRouteUpdate(protect)
}
function beforeUnload(event: BeforeUnloadEvent) { if (dirty.value) { event.preventDefault(); event.returnValue = '' } }
window.addEventListener('beforeunload', beforeUnload)
onBeforeUnmount(() => { navigation?.(false); window.removeEventListener('beforeunload', beforeUnload) })
</script>

<template>
  <section
    v-show="folderReady || opened"
    aria-label="Role defaults"
    class="rounded-lg border border-default bg-bg-surface p-4"
  >
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 class="t-h3">
          Role defaults
        </h2>
        <p class="text-xs text-fg-muted">
          Delivery, review and main agents for fresh invocations.
        </p>
      </div>
      <UiButton
        v-if="!opened"
        :disabled="!folderReady || scopeChanged"
        @click="open"
      >
        Configure roles
      </UiButton>
    </div>
    <UiCallout
      v-if="opened && !folderReady"
      tone="warning"
      class="mt-3"
    >
      The project folder is being checked or is unavailable. Your role drafts are retained. Refresh the folder before saving.
    </UiCallout>
    <UiCallout
      v-else-if="opened && scopeChanged"
      tone="warning"
      class="mt-3"
    >
      The project folder changed. Your role drafts are retained. Leave this page and reopen Agents to use the selected folder.
    </UiCallout>
    <form
      v-if="opened"
      class="mt-4 space-y-3"
      aria-label="Role settings"
      @submit.prevent="settings.save"
    >
      <UiFormField label="Role">
        <UiSelect
          :model-value="role"
          :disabled="saving || loading || !folderReady || scopeChanged"
          :options="[{ value: 'delivery_agent', label: 'Delivery' }, { value: 'review_agent', label: 'Review' }, { value: 'main_agent', label: 'Main' }]"
          @update:model-value="selectRole"
        />
      </UiFormField>
      <p
        v-if="loading"
        role="status"
        class="text-fg-muted"
      >
        Loading saved settings…
      </p>
      <template v-else>
        <p
          v-if="checking"
          class="text-xs text-fg-muted"
        >
          Checking effective saved configuration…
        </p>
        <p
          v-else-if="current"
          aria-label="Effective saved configuration"
          class="break-words text-xs text-fg-muted"
        >
          Effective saved: {{ effectiveText }}
        </p>
        <p
          v-if="effectiveError"
          role="alert"
          class="text-xs text-danger-fg"
        >
          Effective saved configuration unavailable: {{ effectiveError }}
        </p>
        <p
          v-if="hostStatuses.kind === 'error'"
          role="alert"
          class="text-xs text-danger-fg"
        >
          {{ hostStatuses.message }}
        </p>
        <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
          <UiFormField
            label="Agent app"
            help="Changing the app clears incompatible launch choices. Returning restores this role's draft."
          >
            <UiSelect
              :model-value="draft.host_key || ''"
              :options="hostOptions"
              :disabled="!ready"
              @update:model-value="changeHost"
            />
          </UiFormField>
          <UiFormField
            label="Model fallback"
            help="An explicit Codex source model takes precedence over this fallback."
          >
            <UiSelect
              :model-value="customModel ? '__custom__' : draft.model || ''"
              :options="modelOptions"
              :disabled="!ready"
              @update:model-value="changeModel"
            />
            <UiInput
              v-if="customModel"
              :model-value="draft.model || ''"
              aria-label="Custom role model"
              :disabled="!ready"
              @update:model-value="settings.set('model', String($event) || null)"
            />
          </UiFormField>
          <UiFormField
            label="Reasoning fallback"
            help="An explicit Codex source effort takes precedence. Empty uses native inheritance."
          >
            <UiSelect
              :model-value="draft.model_reasoning_effort || ''"
              :options="efforts"
              :disabled="!ready"
              @update:model-value="settings.set('model_reasoning_effort', String($event) || null)"
            />
          </UiFormField>
          <UiFormField
            v-if="catalog?.provider_selection"
            label="Provider"
            help="Native configuration name; no credentials."
          >
            <UiInput
              :model-value="draft.provider || ''"
              :disabled="!ready"
              @update:model-value="settings.set('provider', String($event) || null)"
            />
          </UiFormField>
          <UiFormField
            v-if="catalog?.profile_selection"
            label="Agent profile"
            help="Native profile name; empty inherits."
          >
            <UiInput
              :model-value="draft.profile || ''"
              :disabled="!ready"
              @update:model-value="settings.set('profile', String($event) || null)"
            />
          </UiFormField>
        </div>
        <p
          v-if="role === 'main_agent'"
          class="text-xs text-fg-muted"
        >
          The setup or automation supplies the main agent contract. These defaults change its launch choices.
        </p>
        <template v-else>
          <UiFormField
            v-if="hostKey === 'codex'"
            label="Codex role source"
            help="Native source editing is Codex-only. Edit the selected file in the project definitions below."
          >
            <UiSelect
              :model-value="selectedSource"
              :options="sourceOptions"
              :disabled="!ready"
              @update:model-value="changeSource"
            />
          </UiFormField>
          <template v-else>
            <UiFormField label="Agent name">
              <UiInput
                :model-value="draft.name || ''"
                :disabled="!ready"
                @update:model-value="settings.set('name', String($event))"
              />
            </UiFormField>
            <UiFormField
              label="Role instructions"
              help="This host uses its supported inline contract."
            >
              <UiTextarea
                :model-value="draft.instructions || ''"
                :disabled="!ready"
                :rows="5"
                @update:model-value="settings.set('instructions', String($event))"
              />
            </UiFormField>
          </template>
        </template>
        <UiCallout
          v-if="error"
          tone="danger"
        >
          {{ error }}
        </UiCallout>
        <p
          v-if="message && ready"
          role="status"
          class="text-xs text-success-fg"
        >
          {{ message }}
        </p>
        <div class="flex flex-wrap items-center gap-2">
          <UiButton
            type="submit"
            variant="primary"
            :disabled="!ready || !roleDirty"
            :loading="saving"
          >
            Save role
          </UiButton>
          <UiButton
            :disabled="loading || saving || !folderReady || scopeChanged"
            @click="settings.load"
          >
            {{ dirty ? 'Reload and retain edits' : 'Reload saved settings' }}
          </UiButton>
          <UiButton
            v-if="hostStatuses.kind === 'error'"
            @click="loadHostStatuses"
          >
            Retry agent apps
          </UiButton>
          <span class="text-xs text-fg-muted">{{ dirty ? 'Unsaved changes' : 'No changes' }} · Saving never starts work.</span>
        </div>
      </template>
    </form>
    <UiConfirmDialog
      :model-value="confirmLeave"
      title="Discard role changes?"
      message="Your unsaved role settings will be discarded."
      confirm-label="Discard changes"
      @confirm="finishNavigation(true)"
      @cancel="finishNavigation(false)"
      @update:model-value="!$event && finishNavigation(false)"
    />
  </section>
</template>
