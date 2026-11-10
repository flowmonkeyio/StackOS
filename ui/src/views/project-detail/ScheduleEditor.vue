<script setup lang="ts">
import { computed, ref } from 'vue'
import type { SchemaWorkflowTemplateListOut } from '@/api'
import { UiButton, UiCallout, UiCheckbox, UiDialog, UiFormField, UiInput, UiSelect, UiTextarea } from '@/components/ui'
import { apiFetch, formatApiError } from '@/lib/client'
import { useSchedulesStore, type ScheduledJob, type ScheduleConfiguration } from '@/stores/schedules'
import ScheduleAgentSelectors from './ScheduleAgentSelectors.vue'

const props = defineProps<{ projectId: number; job: ScheduledJob | null }>()
const emit = defineEmits<{ close: []; saved: [] }>()
const store = useSchedulesStore()
const job = props.job
const kind = job?.kind ?? `automation-${crypto.randomUUID()}`
const name = ref(job?.name ?? '')
const templateKey = ref(job?.template_key ?? '')
const cron = ref(job?.cron_expr ?? '0 9 * * *')
const timezone = ref(job?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone)
const projectDir = ref(job?.execution_json?.project_dir ?? '')
const enabled = ref(job?.enabled ?? false)
const inputs = ref(JSON.stringify(job?.inputs_json ?? {}, null, 2))
const existingOptions = { ...job?.execution_json }
delete existingOptions.project_dir
delete existingOptions.agents
delete existingOptions.step_agent_assignments
const agents = ref({ ...job?.execution_json?.agents })
for (const role of ['delivery_agent', 'review_agent'] as const) {
  if (existingOptions[role]?.source_ref) {
    agents.value[role] ??= existingOptions[role]
    delete existingOptions[role]
  }
}
const assignments = ref({ ...job?.execution_json?.step_agent_assignments })
const selectors = ref<InstanceType<typeof ScheduleAgentSelectors> | null>(null)
const executionOptions = ref(JSON.stringify(existingOptions, null, 2))
const hostKey = computed(() => {
  try { return String(objectJson(executionOptions.value, 'Execution options').host_key ?? 'codex') }
  catch { return 'codex' }
})
const error = ref<string | null>(null)
const workflows = ref<SchemaWorkflowTemplateListOut['templates']>([])
const workflowError = ref<string | null>(null)
const workflowOptions = computed(() => {
  const options = workflows.value.map(item => ({ value: item.key, label: item.name }))
  if (templateKey.value && !options.some(item => item.value === templateKey.value)) options.push({ value: templateKey.value, label: templateKey.value })
  return options
})
void apiFetch<SchemaWorkflowTemplateListOut>(`/api/v1/projects/${props.projectId}/workflow-templates`)
  .then(result => { workflows.value = result.templates ?? [] })
  .catch(cause => { workflowError.value = formatApiError(cause) })

function objectJson(value: string, label: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value)
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error(`${label} must be a JSON object.`)
  return parsed as Record<string, unknown>
}

async function save(): Promise<void> {
  if (store.saving) return
  error.value = null
  try {
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
        ...(Object.keys(agents.value).length ? { agents: agents.value } : {}),
        ...(Object.keys(assignments.value).length ? { step_agent_assignments: assignments.value } : {}),
      } : null,
    }
    await store.save(props.projectId, configuration)
    emit('saved')
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
    <form
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
      <UiFormField
        label="Working directory"
        help="The existing folder where the configured agents will work."
      >
        <UiInput
          v-model="projectDir"
          :disabled="store.saving"
        />
      </UiFormField>
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
      >
        Save automation
      </UiButton>
    </template>
  </UiDialog>
</template>
