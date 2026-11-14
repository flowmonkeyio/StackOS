<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, useId } from 'vue'
import type { SchemaMainAgentPreferences, SchemaProjectSetupOut, SchemaSetupAttempt, SchemaSetupWorkflowOut } from '@/api'
import { UiButton, UiCallout, UiDialog, UiFormField, UiInput, UiSelect } from '@/components/ui'
import StatusBadge from '@/components/StatusBadge.vue'
import { resolveStatus } from '@/design/status'
import type { DesktopMcpHostStatus } from '@/lib/desktop'
import { callOperation } from '@/lib/operations'
import { readProjectSetup, setupErrorMessage } from './useProjectSetup'

const props = defineProps<{
  state: SchemaProjectSetupOut
  pending: boolean
  error: string | null
  start: (preferences: SchemaMainAgentPreferences) => Promise<SchemaSetupAttempt | null>
}>()
const emit = defineEmits<{ close: []; started: [] }>()
const hosts = ref<DesktopMcpHostStatus[]>([])
const loading = ref(true)
const catalogError = ref<string | null>(null)
const saved = props.state.launch_preferences ?? {}
const hostKey = ref(typeof saved.host_key === 'string' ? saved.host_key : '')
const model = ref(typeof saved.model === 'string' ? saved.model : '')
const reasoning = ref(typeof saved.model_reasoning_effort === 'string' ? saved.model_reasoning_effort : '')
const provider = ref(typeof saved.provider === 'string' ? saved.provider : '')
const profile = ref(typeof saved.profile === 'string' ? saved.profile : '')
const changing = ref(!hostKey.value)
const customModel = ref(false)
const advanced = ref(false)
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
const reasoningChoices = computed(() => catalog.value?.reasoning_efforts?.[model.value] ?? [])
const reasoningOptions = computed(() => [
  { value: '', label: 'Agent default' },
  ...reasoningChoices.value.map(value => ({ value, label: value })),
])
const selectionError = computed(() => reasoning.value && !reasoningChoices.value.includes(reasoning.value)
  ? `Reasoning ${reasoning.value} is unavailable for this model. Choose a supported level or Agent default.` : null)
const ready = computed(() => props.state.folder_status === 'available' && host.value?.available && !selectionError.value
  && (!usesCustomModel.value || !!model.value.trim())
  && (!catalog.value?.requires_provider_model || (provider.value.trim() && model.value.trim())))
const hostOptions = computed(() => supported.value.map(item => ({ value: item.host_key, label: item.display_name || item.host_key, disabled: !item.available })))
const modelOptions = computed(() => {
  const options = (catalog.value?.models ?? []).map(value => ({ value, label: value === catalog.value?.recommended_model ? `${value} (Recommended)` : value }))
  if (!catalog.value?.custom_models && model.value && !options.some(option => option.value === model.value)) options.push({ value: model.value, label: model.value })
  if (!catalog.value?.requires_provider_model) options.unshift({ value: '', label: 'Agent default' })
  if (catalog.value?.custom_models) options.push({ value: CUSTOM_MODEL, label: 'Enter custom model' })
  return options
})
function changeModel(value: string | number | null): void {
  if (String(value ?? '') === selectedModel.value) return
  customModel.value = value === CUSTOM_MODEL
  if (!customModel.value) model.value = String(value ?? '')
  else model.value = ''
  reasoning.value = ''
}
function changeCustomModel(value: string | number | null): void {
  if (String(value ?? '') === model.value) return
  model.value = String(value ?? '')
  reasoning.value = ''
}
function appliedVersions(successful: Record<string, unknown> | null): string {
  const verification = successful?.verification
  if (!verification || typeof verification !== 'object' || !('sources' in verification) || !Array.isArray(verification.sources)) return 'Not verified'
  return verification.sources.map((source: { key?: string; version?: string | null }) => `${source.key ?? 'Guidance'}: ${source.version ?? 'Unversioned'}`).join(' · ')
}
function workflowName(workflow: SchemaSetupWorkflowOut): string {
  const previous = workflow.successful?.verification as { sources?: { kind?: string; content?: { name?: string } }[] } | undefined
  const previousSources = Array.isArray(previous?.sources) ? previous.sources : []
  const source = workflow.available_sources?.find(source => source.kind === 'template')
    ?? previousSources.find(source => source?.kind === 'template')
  return typeof source?.content?.name === 'string' ? source.content.name : workflow.workflow_key
}
function workflowStatus(workflow: SchemaSetupWorkflowOut): string {
  return workflow.status === 'source_unavailable' ? workflow.status
    : workflow.latest_status && workflow.latest_status !== 'success' ? workflow.latest_status : workflow.status
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
  reasoning.value = ''
  advanced.value = false
}
async function loadHosts(): Promise<void> {
  loading.value = true
  catalogError.value = null
  try {
    const result = await callOperation<{ items: DesktopMcpHostStatus[] }>('hostMcp.status', {})
    if (disposed) return
    hosts.value = result.items
    // Initial host inheritance belongs to the launch resolver, not the user
    // change handler (which intentionally clears incompatible selections).
    if (!hostKey.value) {
      const preview = await readProjectSetup(props.state.project_id, true)
      if (disposed) return
      const effective = preview.launch_preferences ?? {}
      const effectiveHost = typeof effective.host_key === 'string' ? effective.host_key : ''
      if (Object.keys(saved).length) {
        hostKey.value = effectiveHost
        model.value = typeof effective.model === 'string' ? effective.model : ''
        reasoning.value = typeof effective.model_reasoning_effort === 'string' ? effective.model_reasoning_effort : ''
        provider.value = typeof effective.provider === 'string' ? effective.provider : ''
        profile.value = typeof effective.profile === 'string' ? effective.profile : ''
      } else changeHost(effectiveHost)
    }
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
  const result = await props.start({ host_key: hostKey.value, model: model.value.trim() || null,
    model_reasoning_effort: reasoning.value || null, provider: provider.value.trim() || null, profile: profile.value.trim() || null })
  if (!disposed && result) emit('started')
}
</script>

<template>
  <UiDialog
    :model-value="true"
    :title="state.status === 'updates_available' ? 'Update project workflows' : 'Set up this project'"
    size="lg"
    layout="detail"
    detail-height="720px"
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
          <span
            class="min-w-0 flex-1 break-words"
            :title="workflow.workflow_key"
          >{{ workflowName(workflow) }}</span>
          <StatusBadge
            domain="projectSetup"
            :status="workflowStatus(workflow)"
          />
          <p
            v-if="workflow.status === 'source_unavailable' || workflow.latest_status === 'failed'"
            class="basis-full break-words text-fg-muted"
          >
            {{ workflow.diagnostic || resolveStatus('projectSetup', workflowStatus(workflow)).description }}
          </p>
        </div>
      </div>
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
              {{ workflowName(workflow) }}
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
            {{ host?.display_name || hostKey || 'No supported agent app detected' }} · Model: {{ model || 'Agent default' }} · Reasoning: {{ reasoning || 'Agent default' }}
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
        <UiCallout
          v-if="selectionError"
          tone="warning"
        >
          {{ selectionError }}
        </UiCallout>
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
              :model-value="model"
              :class="catalog?.models.length ? 'mt-2' : ''"
              :disabled="pending"
              placeholder="Enter model name or alias"
              aria-label="Custom model"
              required
              @update:model-value="changeCustomModel"
            />
          </UiFormField>
          <UiFormField
            label="Reasoning"
            :help="reasoningChoices.length ? 'Applies to this launch and its continuations.' : model ? 'This model uses native reasoning; no verified override is available.' : 'Choose a model to see supported reasoning levels.'"
          >
            <UiSelect
              v-model="reasoning"
              :options="reasoningOptions"
              :disabled="pending"
            />
          </UiFormField>
          <UiFormField
            v-if="catalog?.requires_provider_model"
            label="Provider"
            help="Use the provider configured in your agent app. This is a native configuration name, not a credential."
          >
            <UiInput
              v-model="provider"
              :disabled="pending"
            />
          </UiFormField>
          <div
            v-if="(catalog?.provider_selection && !catalog.requires_provider_model) || catalog?.profile_selection"
            class="space-y-3"
          >
            <UiButton
              variant="ghost"
              size="sm"
              :aria-expanded="advanced"
              :disabled="pending"
              @click="advanced = !advanced"
            >
              Advanced options
            </UiButton>
            <template v-if="advanced">
              <UiFormField
                v-if="catalog?.provider_selection && !catalog.requires_provider_model"
                label="Provider"
                help="Optional native provider configuration name; empty uses the agent app default. No credentials."
              >
                <UiInput
                  v-model="provider"
                  :disabled="pending"
                />
              </UiFormField>
              <UiFormField
                v-if="catalog?.profile_selection"
                label="Agent profile"
                help="Optional native agent configuration profile; empty uses the agent app default. No credentials."
              >
                <UiInput
                  v-model="profile"
                  :disabled="pending"
                />
              </UiFormField>
            </template>
          </div>
          <p class="text-xs text-fg-muted">
            Choices apply to this setup only. Saved project defaults are managed separately.
          </p>
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
