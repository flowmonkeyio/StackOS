<script setup lang="ts">
import { computed, inject, onBeforeUnmount, ref, toRef } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, routerKey } from 'vue-router'
import type { SchemaWorkflowTemplateListOut } from '@/api'
import { UiButton, UiCallout, UiCheckbox, UiConfirmDialog, UiDialog, UiFormField, UiInput, UiLoadingState, UiSelect } from '@/components/ui'
import { useProjectFolder } from '@/composables/useProjectFolder'
import { apiFetch, formatApiError } from '@/lib/client'
import { useSchedulesStore, type ScheduledJob, type ScheduleConfiguration } from '@/stores/schedules'
import ScheduleAgentSelectors from './ScheduleAgentSelectors.vue'
import ScheduleWorkflowInputs from './ScheduleWorkflowInputs.vue'
import { effectiveInputs, fieldError, workflowFields, type InputValues } from './scheduleInputs'
import { useScheduleWorkflow } from './useScheduleWorkflow'

const props = defineProps<{ projectId: number; job: ScheduledJob | null }>()
const emit = defineEmits<{ close: []; saved: [] }>()
const store = useSchedulesStore()
const editorProjectId = props.projectId
const scopeChanged = computed(() => editorProjectId !== props.projectId)
const { projectDir: configuredDir, folderIssue, folderLoading, refreshFolder } = useProjectFolder(toRef(props, 'projectId'))
const job = props.job
const kind = job?.kind ?? `automation-${crypto.randomUUID()}`
const name = ref(job?.name ?? '')
const templateKey = ref(job?.template_key ?? '')
const cron = ref(job?.cron_expr ?? '0 9 * * *')
const timezone = ref(job?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone)
const savedTarget = ref(job?.execution_json?.project_dir ?? null)
const projectDir = computed(() => scopeChanged.value ? '' : savedTarget.value ?? configuredDir.value)
const targetMismatch = computed(() => !!savedTarget.value && !!configuredDir.value && savedTarget.value !== configuredDir.value)
const confirmTargetChange = ref(false)
function reconcileTarget() {
  if (!scopeChanged.value && configuredDir.value) savedTarget.value = configuredDir.value
  confirmTargetChange.value = false
}
const enabled = ref(job?.enabled ?? false)
const inputOverrides = ref<InputValues>({ ...job?.inputs_json })
const { state: workflowState, refresh: refreshWorkflow } = useScheduleWorkflow(toRef(props, 'projectId'), templateKey, () => !scopeChanged.value)
const inputs = computed(() => effectiveInputs(workflowState.value.workflow, inputOverrides.value))
function updateInput(key: string, value: unknown) { inputOverrides.value = { ...inputOverrides.value, [key]: value } }
const submitted = ref(false)
const pendingTemplate = ref<string | null>(null)
function changeWorkflow(value: string | number | null) {
  const next = String(value ?? '')
  if (next === templateKey.value) return
  if (Object.keys(inputs.value).length) pendingTemplate.value = next
  else selectWorkflow(next)
}
function selectWorkflow(next: string) {
  templateKey.value = next
  inputOverrides.value = {}
  submitted.value = false
  error.value = null
  pendingTemplate.value = null
  void refreshWorkflow()
}
const existingOptions = { ...job?.execution_json }
delete existingOptions.project_dir
delete existingOptions.agents
delete existingOptions.step_agent_assignments
type AgentBindings = NonNullable<NonNullable<ScheduleConfiguration['execution_json']>['agents']>
const savedAgents = ref<AgentBindings>({ ...job?.execution_json?.agents })
const legacyRoles = ['delivery_agent', 'review_agent'] as const
const assignments = ref({ ...job?.execution_json?.step_agent_assignments })
const selectors = ref<InstanceType<typeof ScheduleAgentSelectors> | null>(null)
const executionOptions = ref(existingOptions)
const error = ref<string | null>(null)
const agents = computed<AgentBindings>({
  get() {
    const effective = { ...savedAgents.value }
    const options = executionOptions.value
    // Match schedule admission: a nonempty legacy role replaces its mapped choice.
    for (const role of legacyRoles) {
      const legacy = options[role] as AgentBindings[string] | null | undefined
      if (legacy && Object.keys(legacy).length) effective[role] = legacy
    }
    return effective
  },
  set(next) {
    const options = { ...executionOptions.value }
    const current = agents.value
    const updated = { ...savedAgents.value }
    let changedLegacy = false
    for (const role of new Set([...Object.keys(current), ...Object.keys(next)])) {
      if (JSON.stringify(current[role]) === JSON.stringify(next[role])) continue
      if (next[role]) updated[role] = next[role]; else delete updated[role]
      const legacyRole = legacyRoles.find(key => key === role)
      if (legacyRole && legacyRole in options) {
        delete options[legacyRole]
        changedLegacy = true
      }
    }
    savedAgents.value = updated
    if (changedLegacy) executionOptions.value = options
  },
})
const hostKey = computed(() => executionOptions.value.host_key ?? 'codex')
const executionSummary = computed(() => [
  `Agent app: ${hostKey.value}`,
  ...Object.entries(agents.value).map(([role, agent]) => `${role}: ${[['App', agent.host_key], ['Model', agent.model], ['Effort', agent.model_reasoning_effort], ['Provider', agent.provider], ['Profile', agent.profile]].filter(([, value]) => value != null).map(([label, value]) => `${label} ${value}`).join(' · ') || 'project defaults'}`),
  ...(executionOptions.value.mcp_config_ref ? [`MCP configuration: ${executionOptions.value.mcp_config_ref.path} (${executionOptions.value.mcp_config_ref.sha256})`] : []),
])
const workflows = ref<SchemaWorkflowTemplateListOut['templates']>([])
const workflowError = ref<string | null>(null)
const workflowOptions = computed(() => {
  const options = workflows.value.map(item => ({ value: item.key, label: item.name }))
  if (templateKey.value && !options.some(item => item.value === templateKey.value)) options.push({ value: templateKey.value, label: templateKey.value })
  return options
})
let disposed = false
onBeforeUnmount(() => { disposed = true })
void apiFetch<SchemaWorkflowTemplateListOut>(`/api/v1/projects/${editorProjectId}/workflow-templates`)
  .then(result => { if (!disposed && !scopeChanged.value) workflows.value = result.templates ?? [] })
  .catch(cause => { if (!disposed && !scopeChanged.value) workflowError.value = formatApiError(cause) })

const draftIdentity = () => JSON.stringify([name.value, templateKey.value, cron.value, timezone.value, enabled.value, savedTarget.value, inputOverrides.value, savedAgents.value, assignments.value, executionOptions.value])
const baseline = ref(draftIdentity())
const dirty = computed(() => draftIdentity() !== baseline.value)
const discardAction = ref<(() => void) | null>(null)
let cancelNavigation: (() => void) | null = null
function requestClose() {
  if (store.saving) return
  if (dirty.value) discardAction.value = () => emit('close')
  else emit('close')
}
function discard() {
  const action = discardAction.value
  discardAction.value = null
  action?.()
}
function cancelDiscard() { cancelNavigation?.(); cancelNavigation = null; discardAction.value = null }
if (inject(routerKey, null)) {
  const protectNavigation = () => {
    if (store.saving) return false
    if (!dirty.value) return true
    cancelNavigation?.()
    return new Promise<boolean>(resolve => {
      cancelNavigation = () => resolve(false)
      discardAction.value = () => { cancelNavigation = null; resolve(true) }
    })
  }
  onBeforeRouteLeave(protectNavigation)
  onBeforeRouteUpdate(protectNavigation)
}
function beforeUnload(event: BeforeUnloadEvent) { if (dirty.value) { event.preventDefault(); event.returnValue = '' } }
window.addEventListener('beforeunload', beforeUnload)
onBeforeUnmount(() => { cancelNavigation?.(); window.removeEventListener('beforeunload', beforeUnload) })

async function save(): Promise<void> {
  if (store.saving) return
  error.value = null
  submitted.value = true
  try {
    if (scopeChanged.value || disposed) throw new Error('Project changed. Reopen this automation from the current project.')
    if (folderLoading.value && !savedTarget.value) throw new Error('Wait for the project folder to load.')
    if (enabled.value && !projectDir.value) throw new Error(folderIssue.value || 'Set the project folder once in project setup before enabling this automation.')
    if (workflowState.value.loading) throw new Error('Wait for the workflow to load.')
    if (workflowState.value.error) throw new Error(workflowState.value.error)
    if (enabled.value && !templateKey.value) throw new Error('Choose a workflow before enabling this automation.')
    const invalid = workflowState.value.workflow && workflowFields(workflowState.value.workflow).some(field => fieldError(field, inputs.value[field.key], enabled.value))
    if (invalid) return
    const options = executionOptions.value
    const selectionError = selectors.value?.validate()
    if (selectionError) throw new Error(selectionError)
    const configuration: ScheduleConfiguration = {
      kind,
      name: name.value.trim() || null,
      template_key: templateKey.value || null,
      cron_expr: cron.value.trim(), timezone: timezone.value.trim(), enabled: enabled.value,
      inputs_json: inputs.value,
      execution_json: projectDir.value.trim() || Object.keys(options).length || Object.keys(savedAgents.value).length || Object.keys(assignments.value).length ? {
        ...options, project_dir: projectDir.value.trim(),
        ...(Object.keys(savedAgents.value).length ? { agents: savedAgents.value } : {}),
        ...(Object.keys(assignments.value).length ? { step_agent_assignments: assignments.value } : {}),
      } : null,
    }
    await store.save(editorProjectId, configuration)
    if (!disposed && !scopeChanged.value) { baseline.value = draftIdentity(); emit('saved') }
  } catch (cause) { error.value = formatApiError(cause) }
}
</script>

<template>
  <UiDialog
    :model-value="true"
    :title="job ? 'Edit automation' : 'New automation'"
    description="Runs only while StackOS is open. Missed runs are skipped."
    size="lg"
    layout="detail"
    @update:model-value="!$event && requestClose()"
  >
    <UiCallout
      v-if="scopeChanged"
      tone="warning"
      title="Project changed"
    >
      Reopen this automation from the current project.
    </UiCallout>
    <form
      v-else
      id="schedule-editor"
      class="space-y-4"
      novalidate
      @submit.prevent="save"
    >
      <UiCallout
        v-if="error"
        tone="danger"
        title="Could not save automation"
      >
        {{ error }}
      </UiCallout>
      <UiFormField label="Name">
        <UiInput
          v-model="name"
          required
          :disabled="store.saving"
        />
      </UiFormField>
      <UiFormField
        label="Workflow"
        :error="workflowError ?? undefined"
      >
        <UiSelect
          :model-value="templateKey"
          :options="workflowOptions"
          placeholder="Choose a workflow"
          searchable
          :disabled="store.saving"
          @update:model-value="changeWorkflow"
        />
      </UiFormField>
      <div class="grid grid-cols-2 gap-4">
        <UiFormField
          label="Schedule"
          help="Five-field cron: minute hour day month weekday. Monday = 0."
        >
          <UiInput
            v-model="cron"
            required
            :disabled="store.saving"
          />
        </UiFormField>
        <UiFormField
          label="Timezone"
          help="IANA name, for example America/Los_Angeles."
        >
          <UiInput
            v-model="timezone"
            required
            :disabled="store.saving"
          />
        </UiFormField>
      </div>
      <UiLoadingState
        v-if="folderLoading"
        label="Loading project folder…"
      />
      <UiCallout
        v-else-if="folderIssue"
        tone="warning"
        title="Project folder needs setup"
      >
        {{ folderIssue }}
        <UiButton @click="refreshFolder">
          Retry project folder
        </UiButton>
      </UiCallout>
      <UiCallout
        v-else-if="targetMismatch"
        tone="warning"
        title="Automation uses a different folder"
      >
        This automation keeps its saved folder, {{ savedTarget }}. The configured project folder is {{ configuredDir }}. Other edits keep the saved folder.
        <UiButton
          :disabled="store.saving"
          @click="confirmTargetChange = true"
        >
          Use project folder
        </UiButton>
      </UiCallout>
      <p
        v-if="projectDir"
        class="text-sm text-fg-muted break-all"
      >
        Project folder: {{ projectDir }}
      </p>
      <UiLoadingState
        v-if="workflowState.loading"
        label="Loading workflow fields…"
      />
      <UiCallout
        v-else-if="workflowState.error"
        tone="warning"
        title="Workflow unavailable"
      >
        {{ workflowState.error }}
        <UiButton @click="refreshWorkflow">
          Retry workflow
        </UiButton>
      </UiCallout>
      <ScheduleWorkflowInputs
        v-else-if="workflowState.workflow"
        :model-value="inputs"
        :workflow="workflowState.workflow"
        :enabling="enabled"
        :submitted="submitted"
        :disabled="store.saving"
        @change="updateInput"
      />
      <ScheduleAgentSelectors
        ref="selectors"
        v-model:agents="agents"
        v-model:assignments="assignments"
        :project-id="projectId"
        :project-dir="projectDir.trim()"
        :template-key="templateKey"
        :host-key="hostKey"
        :workflow-state="workflowState"
        :disabled="store.saving"
      />
      <details v-if="Object.keys(executionOptions).length || Object.keys(agents).length">
        <summary class="focus-ring cursor-pointer rounded-sm text-sm text-fg-muted">
          Execution options
        </summary>
        <ul class="mt-3 space-y-1 text-sm text-fg-muted break-all">
          <li
            v-for="line in executionSummary"
            :key="line"
          >
            {{ line }}
          </li>
        </ul>
        <p class="mt-2 text-xs text-fg-muted">
          Saved execution settings are preserved. Use project setup to change advanced choices.
        </p>
      </details>
      <UiCheckbox
        v-model="enabled"
        label="Enabled"
        :disabled="store.saving"
      />
      <p class="text-xs text-fg-muted">
        Disabled drafts stay saved. Enabling checks workflow inputs and execution setup. Existing approvals still apply.
      </p>
    </form>
    <template #footer>
      <UiButton
        variant="ghost"
        :disabled="store.saving"
        @click="requestClose"
      >
        Cancel
      </UiButton>
      <UiButton
        type="submit"
        form="schedule-editor"
        variant="primary"
        :loading="store.saving"
        :disabled="scopeChanged || (folderLoading && !savedTarget)"
      >
        Save automation
      </UiButton>
    </template>
  </UiDialog>
  <UiConfirmDialog
    :model-value="confirmTargetChange && !scopeChanged"
    title="Use the configured project folder?"
    :description="`Change this automation from ${savedTarget} to ${configuredDir}. Its saved agent selections will be checked in that folder when you save.`"
    confirm-label="Use project folder"
    @confirm="reconcileTarget"
    @update:model-value="confirmTargetChange = $event"
  />
  <UiConfirmDialog
    :model-value="pendingTemplate !== null && !scopeChanged"
    title="Change workflow?"
    description="Saved and edited workflow input values will be cleared. Agent choices will be checked against the selected workflow."
    confirm-label="Change workflow"
    @confirm="selectWorkflow(pendingTemplate ?? '')"
    @update:model-value="!$event && (pendingTemplate = null)"
  />
  <UiConfirmDialog
    :model-value="!!discardAction"
    title="Discard automation edits?"
    description="Your unsaved automation changes will be lost."
    confirm-label="Discard edits"
    @confirm="discard"
    @update:model-value="!$event && cancelDiscard()"
  />
</template>
