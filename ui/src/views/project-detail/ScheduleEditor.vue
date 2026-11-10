<script setup lang="ts">
import { computed, onBeforeUnmount, ref, toRef } from 'vue'
import type { SchemaWorkflowTemplateListOut } from '@/api'
import { UiButton, UiCallout, UiCheckbox, UiConfirmDialog, UiDialog, UiFormField, UiInput, UiLoadingState, UiSelect, UiTextarea } from '@/components/ui'
import { useProjectFolder } from '@/composables/useProjectFolder'
import { apiFetch, formatApiError } from '@/lib/client'
import { useSchedulesStore, type ScheduledJob, type ScheduleConfiguration } from '@/stores/schedules'
import ScheduleAgentSelectors from './ScheduleAgentSelectors.vue'

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
const inputs = ref(JSON.stringify(job?.inputs_json ?? {}, null, 2))
const existingOptions = { ...job?.execution_json }
delete existingOptions.project_dir
delete existingOptions.agents
delete existingOptions.step_agent_assignments
type AgentBindings = NonNullable<NonNullable<ScheduleConfiguration['execution_json']>['agents']>
const savedAgents = ref<AgentBindings>({ ...job?.execution_json?.agents })
const legacyRoles = ['delivery_agent', 'review_agent'] as const
const assignments = ref({ ...job?.execution_json?.step_agent_assignments })
const selectors = ref<InstanceType<typeof ScheduleAgentSelectors> | null>(null)
const executionOptions = ref(JSON.stringify(existingOptions, null, 2))
const error = ref<string | null>(null)
const agents = computed<AgentBindings>({
  get() {
    const effective = { ...savedAgents.value }
    try {
      const options = objectJson(executionOptions.value, 'Execution options')
      // Match schedule admission: a nonempty legacy role replaces its mapped choice.
      for (const role of legacyRoles) {
        const legacy = options[role] as AgentBindings[string] | null | undefined
        if (legacy && Object.keys(legacy).length) effective[role] = legacy
      }
    } catch { /* Save reports invalid JSON without discarding the text. */ }
    return effective
  },
  set(next) {
    try {
      const options = objectJson(executionOptions.value, 'Execution options')
      const current = agents.value
      const updated = { ...savedAgents.value }
      let changedLegacy = false
      for (const role of new Set([...Object.keys(current), ...Object.keys(next)])) {
        if (JSON.stringify(current[role]) === JSON.stringify(next[role])) continue
        if (next[role]) updated[role] = next[role]; else delete updated[role]
        if (legacyRoles.some(key => key === role) && role in options) {
          delete options[role]
          changedLegacy = true
        }
      }
      savedAgents.value = updated
      if (changedLegacy) executionOptions.value = JSON.stringify(options, null, 2)
    } catch (cause) { error.value = formatApiError(cause) }
  },
})
const hostKey = computed(() => {
  try { return String(objectJson(executionOptions.value, 'Execution options').host_key ?? 'codex') }
  catch { return 'codex' }
})
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

function objectJson(value: string, label: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value)
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error(`${label} must be a JSON object.`)
  return parsed as Record<string, unknown>
}

async function save(): Promise<void> {
  if (store.saving) return
  error.value = null
  try {
    if (scopeChanged.value || disposed) throw new Error('Project changed. Reopen this automation from the current project.')
    if (folderLoading.value && !savedTarget.value) throw new Error('Wait for the project folder to load.')
    if (enabled.value && !projectDir.value) throw new Error(folderIssue.value || 'Set the project folder once in project setup before enabling this automation.')
    const options = objectJson(executionOptions.value, 'Execution options')
    const selectionError = selectors.value?.validate()
    if (selectionError) throw new Error(selectionError)
    const configuration: ScheduleConfiguration = {
      kind,
      name: name.value.trim() || null,
      template_key: templateKey.value || null,
      cron_expr: cron.value.trim(), timezone: timezone.value.trim(), enabled: enabled.value,
      inputs_json: objectJson(inputs.value, 'Workflow inputs'),
      execution_json: projectDir.value.trim() ? {
        ...options, project_dir: projectDir.value.trim(),
        ...(Object.keys(savedAgents.value).length ? { agents: savedAgents.value } : {}),
        ...(Object.keys(assignments.value).length ? { step_agent_assignments: assignments.value } : {}),
      } : null,
    }
    await store.save(editorProjectId, configuration)
    if (!disposed && !scopeChanged.value) emit('saved')
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
    @update:model-value="!$event && !store.saving && emit('close')"
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
          v-model="templateKey"
          :options="workflowOptions"
          placeholder="Choose a workflow"
          searchable
          :disabled="store.saving"
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
      <ScheduleAgentSelectors
        ref="selectors"
        v-model:agents="agents"
        v-model:assignments="assignments"
        :project-id="projectId"
        :project-dir="projectDir.trim()"
        :template-key="templateKey"
        :host-key="hostKey"
        :disabled="store.saving"
      />
      <UiFormField
        label="Workflow inputs"
        help="Saved input values for the selected workflow, as a JSON object."
      >
        <UiTextarea
          v-model="inputs"
          :rows="4"
          :disabled="store.saving"
        />
      </UiFormField>
      <details>
        <summary class="focus-ring cursor-pointer rounded-sm text-sm text-fg-muted">
          Execution options
        </summary>
        <UiFormField
          class="mt-3"
          label="Agent app and execution context"
          help="Existing agent app, provider/profile and MCP options are preserved. Use the role selectors for project file assignments. Omitted options use project defaults."
        >
          <UiTextarea
            v-model="executionOptions"
            :rows="5"
            :disabled="store.saving"
          />
        </UiFormField>
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
        @click="emit('close')"
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
</template>
