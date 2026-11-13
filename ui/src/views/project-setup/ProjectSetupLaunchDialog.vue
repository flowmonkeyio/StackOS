<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, useId } from 'vue'
import type { SchemaMainAgentPreferences, SchemaProjectSetupOut, SchemaSetupAttempt } from '@/api'
import { UiButton, UiCallout, UiDialog, UiFormField, UiInput, UiSelect } from '@/components/ui'
import type { DesktopAgentExecutionCapabilities, DesktopMcpHostStatus } from '@/lib/desktop'
import { callOperation } from '@/lib/operations'
import { setupErrorMessage } from './useProjectSetup'

type SetupHost = DesktopMcpHostStatus & { agent_execution?: DesktopAgentExecutionCapabilities & { recommended_model?: string | null } }
const props = defineProps<{
  state: SchemaProjectSetupOut
  pending: boolean
  error: string | null
  start: (preferences: SchemaMainAgentPreferences) => Promise<SchemaSetupAttempt | null>
}>()
const emit = defineEmits<{ close: []; started: [] }>()
const hosts = ref<SetupHost[]>([])
const loading = ref(true)
const catalogError = ref<string | null>(null)
const saved = props.state.launch_preferences ?? {}
const hostKey = ref(typeof saved.host_key === 'string' ? saved.host_key : '')
const model = ref(typeof saved.model === 'string' ? saved.model : '')
const provider = ref(typeof saved.provider === 'string' ? saved.provider : '')
const profile = ref(typeof saved.profile === 'string' ? saved.profile : '')
const changing = ref(!hostKey.value)
const customModel = ref(false)
const CUSTOM_MODEL = '__custom_model__'
const customModelId = `setup-custom-model-${useId()}`
let disposed = false
onBeforeUnmount(() => { disposed = true })
const supported = computed(() => hosts.value.filter(host => host.agent_execution?.sandboxes.includes('workspace-write')))
const host = computed(() => supported.value.find(item => item.host_key === hostKey.value))
const catalog = computed(() => host.value?.agent_execution)
const usesCustomModel = computed(() => !!catalog.value?.custom_models && (
  customModel.value || !catalog.value.models.length
  || (!!model.value && !catalog.value.models.includes(model.value))
))
const selectedModel = computed(() => usesCustomModel.value ? CUSTOM_MODEL : model.value)
const retainsSavedHost = computed(() => hostKey.value === saved.host_key)
const clearedSavedChoice = computed(() => retainsSavedHost.value && (
  (typeof saved.provider === 'string' && !provider.value.trim())
  || (typeof saved.profile === 'string' && !profile.value.trim())
))
const ready = computed(() => props.state.folder_status === 'available' && host.value?.available && !clearedSavedChoice.value
  && (!usesCustomModel.value || !!model.value.trim())
  && (!catalog.value?.requires_provider_model || (provider.value.trim() && model.value.trim())))
const hostOptions = computed(() => supported.value.map(item => ({ value: item.host_key, label: item.display_name || item.host_key, disabled: !item.available })))
const modelOptions = computed(() => {
  const options = (catalog.value?.models ?? []).map(value => ({ value, label: value === catalog.value?.recommended_model ? `${value} (Recommended)` : value }))
  if (!catalog.value?.custom_models && model.value && !options.some(option => option.value === model.value)) options.push({ value: model.value, label: model.value })
  // The launch API inherits omitted/null settings on the saved host. Do not
  // offer a "default" choice that would silently keep a saved explicit model.
  if (!catalog.value?.requires_provider_model && !(retainsSavedHost.value && saved.model)) options.unshift({ value: '', label: 'Agent default' })
  if (catalog.value?.custom_models) options.push({ value: CUSTOM_MODEL, label: 'Enter custom model' })
  return options
})
function changeModel(value: string | number | null): void {
  customModel.value = value === CUSTOM_MODEL
  if (!customModel.value) model.value = String(value ?? '')
}
function appliedVersions(successful: Record<string, unknown> | null): string {
  const verification = successful?.verification
  if (!verification || typeof verification !== 'object' || !('sources' in verification) || !Array.isArray(verification.sources)) return 'Not verified'
  return verification.sources.map((source: { key?: string; version?: string | null }) => `${source.key ?? 'Guidance'}: ${source.version ?? 'Unversioned'}`).join(' · ')
}
function changeHost(value: string | number | null): void {
  const next = String(value ?? '')
  if (next === hostKey.value) return
  hostKey.value = next
  model.value = host.value?.agent_execution?.recommended_model ?? ''
  customModel.value = !!host.value?.agent_execution?.custom_models && !!model.value
    && !host.value.agent_execution.models.includes(model.value)
  provider.value = ''
  profile.value = ''
}
async function loadHosts(): Promise<void> {
  loading.value = true
  catalogError.value = null
  try {
    const result = await callOperation<{ items: SetupHost[] }>('hostMcp.status', {})
    if (disposed) return
    hosts.value = result.items
    // A saved choice is never silently replaced, including when unavailable.
    if (!hostKey.value) changeHost(supported.value.find(item => item.available)?.host_key ?? '')
    // Keep explicit custom entry active when its value is cleared or happens
    // to equal a fixed choice; only selecting a listed option leaves this mode.
    if (catalog.value?.custom_models && model.value && !catalog.value.models.includes(model.value)) customModel.value = true
  } catch (cause) {
    if (!disposed) catalogError.value = setupErrorMessage(cause, 'Agent apps could not be checked.')
  } finally { if (!disposed) loading.value = false }
}
onMounted(() => { void loadHosts() })
async function submit(): Promise<void> {
  if (props.pending || !ready.value || loading.value) return
  const result = await props.start({ host_key: hostKey.value, model: model.value || null, provider: provider.value || null, profile: profile.value || null })
  if (!disposed && result) emit('started')
}
</script>

<template>
  <UiDialog
    :model-value="true"
    :title="state.status === 'updates_available' ? 'Update project workflows' : 'Set up this project'"
    size="lg"
    scroll-body
    :static-backdrop="pending"
    :no-escape="pending"
    :hide-close="pending"
    @update:model-value="!pending && emit('close')"
  >
    <div class="space-y-4">
      <UiCallout
        v-if="state.folder_status !== 'available'"
        tone="warning"
      >
        The project folder is unavailable. Restore access, then return to Home and check again. Your setup history is retained.
      </UiCallout>
      <p class="text-fg-muted">
        {{ state.status === 'updates_available' ? `${state.update_count} workflows have new setup guidance.` : 'Let your agent prepare the workflows you need.' }}
      </p>
      <div
        v-if="state.workflows?.length"
        class="divide-y divide-border-subtle rounded-lg border border-default"
      >
        <div
          v-for="workflow in state.workflows"
          :key="workflow.workflow_key"
          class="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-xs"
        >
          <span class="min-w-0 break-words">{{ workflow.workflow_key }}</span>
          <span class="text-fg-muted">{{ workflow.status === 'update_available' ? 'Update available' : workflow.status === 'current' ? 'Current' : workflow.status === 'activation_pending' ? 'Reload pending' : 'Needs checking' }}</span>
        </div>
      </div>
      <p
        v-if="clearedSavedChoice"
        class="text-xs text-fg-muted"
      >
        Enter a replacement for the saved provider or profile, or choose another agent app.
      </p>
      <details
        v-if="state.workflows?.length"
        class="text-xs text-fg-muted"
      >
        <summary class="focus-ring cursor-pointer">
          Version details
        </summary>
        <dl class="mt-2 space-y-3 break-words">
          <div
            v-for="workflow in state.workflows"
            :key="workflow.workflow_key"
          >
            <dt class="font-medium text-fg-default">
              {{ workflow.workflow_key }}
            </dt>
            <dd>Applied: {{ appliedVersions(workflow.successful) }}</dd>
            <dd>Available: {{ workflow.available_sources?.map(source => `${source.key}: ${source.version ?? 'Unversioned'}`).join(' · ') || 'Unavailable' }}</dd>
          </div>
        </dl>
      </details>
      <p
        v-if="loading"
        role="status"
        class="text-fg-muted"
      >
        Checking agent apps…
      </p>
      <UiCallout
        v-else-if="catalogError"
        tone="danger"
      >
        {{ catalogError }}
        <UiButton
          size="sm"
          class="mt-2"
          @click="loadHosts"
        >
          Check again
        </UiButton>
      </UiCallout>
      <template v-else>
        <div class="flex items-start justify-between gap-3 border-t border-subtle pt-4">
          <p class="min-w-0 break-words text-fg-default">
            {{ host?.display_name || hostKey || 'No supported agent app detected' }}<span v-if="model"> · {{ model }}</span>
          </p>
          <UiButton
            v-if="!changing"
            variant="ghost"
            size="sm"
            @click="changing = true"
          >
            Change
          </UiButton>
        </div>
        <div
          v-if="changing && supported.length"
          class="space-y-3"
        >
          <UiFormField label="Agent app">
            <UiSelect
              :model-value="hostKey"
              :options="hostOptions"
              :disabled="pending"
              @update:model-value="changeHost"
            />
          </UiFormField>
          <UiFormField label="Model">
            <UiSelect
              v-if="!catalog?.custom_models || catalog.models.length"
              :model-value="selectedModel"
              :options="modelOptions"
              :disabled="pending"
              @update:model-value="changeModel"
            />
            <UiInput
              v-if="usesCustomModel"
              :id="catalog?.models.length ? customModelId : undefined"
              v-model="model"
              :class="catalog?.models.length ? 'mt-2' : ''"
              :disabled="pending"
              placeholder="Enter model name or alias"
              aria-label="Custom model"
              required
            />
          </UiFormField>
          <UiFormField
            v-if="catalog?.provider_selection"
            label="Provider"
            :help="catalog.requires_provider_model ? 'Use the provider configured in your agent app.' : 'Optional; uses your agent app default.'"
          >
            <UiInput
              v-model="provider"
              :disabled="pending"
            />
          </UiFormField>
          <UiFormField
            v-if="catalog?.profile_selection"
            label="Agent profile"
            help="Optional; uses your agent app default."
          >
            <UiInput
              v-model="profile"
              :disabled="pending"
            />
          </UiFormField>
        </div>
        <UiCallout
          v-if="!host?.available"
          tone="warning"
        >
          {{ hostKey ? 'Your selected agent app is unavailable. Check its setup or choose another detected app.' : 'Install a supported agent app before starting setup.' }}
          <RouterLink
            :to="`/projects/${state.project_id}/agents`"
            class="focus-ring mt-2 block text-fg-link"
          >
            Open agent setup
          </RouterLink>
        </UiCallout>
      </template>
      <p class="text-xs text-fg-muted">
        {{ state.status === 'updates_available' ? 'Your project customizations will be preserved. The agent will ask only if a conflict needs your decision.' : 'The agent will understand your project and ask only for missing goals or preferences.' }}
      </p>
      <p
        v-if="error"
        role="alert"
        class="break-words text-danger-fg"
      >
        {{ error }}
      </p>
    </div>
    <template #footer>
      <UiButton
        variant="ghost"
        :disabled="pending"
        @click="emit('close')"
      >
        Cancel
      </UiButton>
      <UiButton
        variant="primary"
        :disabled="!ready || loading || !!catalogError"
        :loading="pending"
        @click="submit"
      >
        {{ state.status === 'updates_available' ? 'Update workflows' : 'Start setup' }}
      </UiButton>
    </template>
  </UiDialog>
</template>
