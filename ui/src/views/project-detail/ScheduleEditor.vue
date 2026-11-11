<script setup lang="ts">
import { computed, inject, onBeforeUnmount, ref, toRef } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, routerKey } from 'vue-router'
import type { SchemaWorkflowTemplateListOut } from '@/api'
import { UiButton, UiCallout, UiCheckbox, UiConfirmDialog, UiDialog, UiFormField, UiInput, UiLoadingState, UiRadioGroup, UiSelect, UiTextarea } from '@/components/ui'
import { useProjectFolder } from '@/composables/useProjectFolder'
import { apiFetch, formatApiError } from '@/lib/client'
import { useSchedulesStore, type ScheduledJob, type ScheduleConfiguration } from '@/stores/schedules'

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
const mode = ref(job?.template_key ? 'workflow' : 'prompt')
const goal = ref(job?.goal ?? '')
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
// Saved inputs are context for the main agent, independent of workflow schemas.
const inputs = job?.inputs_json ?? null
const submitted = ref(false)
function changeMode(value: string | number) {
  mode.value = String(value)
  if (mode.value === 'prompt') templateKey.value = ''
}
const executionOptions = { ...job?.execution_json }
delete executionOptions.project_dir
const error = ref<string | null>(null)
const workflows = ref<SchemaWorkflowTemplateListOut['templates']>([])
const workflowError = ref<string | null>(null)
const workflowOptions = computed(() => {
  const options = workflows.value.map(item => ({ value: item.key, label: item.name }))
  if (templateKey.value && !options.some(item => item.value === templateKey.value)) options.push({ value: templateKey.value, label: templateKey.value })
  return options
})
const workflowsLoading = ref(false)
let disposed = false
onBeforeUnmount(() => { disposed = true })
async function refreshWorkflows() {
  workflowsLoading.value = true
  workflowError.value = null
  try {
    const result = await apiFetch<SchemaWorkflowTemplateListOut>(`/api/v1/projects/${editorProjectId}/workflow-templates`)
    if (!disposed && !scopeChanged.value) workflows.value = result.templates ?? []
  } catch (cause) {
    if (!disposed && !scopeChanged.value) workflowError.value = formatApiError(cause)
  } finally { if (!disposed) workflowsLoading.value = false }
}
void refreshWorkflows()
const goalError = computed(() => submitted.value && enabled.value && !goal.value.trim() ? 'Enter a goal before enabling this automation.' : undefined)
const templateError = computed(() => submitted.value && mode.value === 'workflow' && !templateKey.value ? 'Choose a workflow or switch to Prompt.' : undefined)

const draftIdentity = () => JSON.stringify([name.value, mode.value, templateKey.value, goal.value, cron.value, timezone.value, enabled.value, savedTarget.value])
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
    if (enabled.value && folderLoading.value) throw new Error('Wait for the project folder to load.')
    if (enabled.value && !projectDir.value) throw new Error(folderIssue.value || 'Set the project folder once in project setup before enabling this automation.')
    if (goalError.value || templateError.value) return
    const configuration: ScheduleConfiguration = {
      kind,
      name: name.value.trim() || null,
      template_key: templateKey.value || null,
      goal: goal.value.trim() || null,
      cron_expr: cron.value.trim(), timezone: timezone.value.trim(), enabled: enabled.value,
      inputs_json: inputs,
      execution_json: savedTarget.value || Object.keys(executionOptions).length ? {
        ...executionOptions, ...(savedTarget.value ? { project_dir: savedTarget.value } : {}),
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
      <UiRadioGroup
        :model-value="mode"
        name="automation-mode"
        aria-label="Automation type"
        orientation="horizontal"
        :options="[{ value: 'workflow', label: 'Workflow' }, { value: 'prompt', label: 'Prompt' }]"
        :disabled="store.saving"
        @update:model-value="changeMode"
      />
      <UiFormField
        v-if="mode === 'workflow'"
        label="Workflow"
        :error="templateError"
      >
        <UiSelect
          v-model="templateKey"
          :options="workflowOptions"
          placeholder="Choose a workflow"
          searchable
          :disabled="store.saving || workflowsLoading"
        />
        <UiLoadingState
          v-if="workflowsLoading"
          label="Loading workflows…"
        />
        <UiCallout
          v-else-if="workflowError"
          tone="warning"
          title="Workflows unavailable"
        >
          {{ workflowError }}
          <UiButton
            :disabled="store.saving"
            @click="refreshWorkflows"
          >
            Retry workflows
          </UiButton>
        </UiCallout>
      </UiFormField>
      <UiFormField
        label="Goal / instructions"
        :error="goalError"
        help="Describe what the project agent should accomplish."
      >
        <UiTextarea
          v-model="goal"
          :rows="5"
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
        <RouterLink
          to="/setup"
          class="focus-ring rounded-sm text-accent-fg"
        >
          Open setup
        </RouterLink>
        Ask your agent to select this project's existing workspace binding.
      </UiCallout>
      <UiCallout
        v-else-if="targetMismatch"
        tone="warning"
        title="Automation uses a different folder"
      >
        Confirm the selected project's folder before enabling this automation. Other edits keep its saved context.
        <UiButton
          :disabled="store.saving"
          @click="confirmTargetChange = true"
        >
          Use project folder
        </UiButton>
      </UiCallout>
      <UiCheckbox
        v-model="enabled"
        label="Enabled"
        :disabled="store.saving"
      />
      <p class="text-xs text-fg-muted">
        Uses the selected project's context. Disabled drafts stay saved. Existing approvals still apply.
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
        :disabled="scopeChanged"
      >
        Save automation
      </UiButton>
    </template>
  </UiDialog>
  <UiConfirmDialog
    :model-value="confirmTargetChange && !scopeChanged"
    title="Use the configured project folder?"
    description="Use the selected project's folder for future runs when you save. Existing runs keep their original context."
    confirm-label="Use project folder"
    @confirm="reconcileTarget"
    @update:model-value="confirmTargetChange = $event"
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
