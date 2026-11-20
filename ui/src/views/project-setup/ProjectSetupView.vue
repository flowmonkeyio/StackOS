<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import type { SchemaSetupAttempt, SchemaSetupWorkflowOut } from '@/api'
import DataTable from '@/components/DataTable.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import type { DataTableColumn } from '@/components/types'
import { ProjectPageHeader } from '@/components/domain'
import { UiButton, UiCard, UiCheckbox, UiDialog, UiPageShell } from '@/components/ui'
import { useProjectRouteScope } from '@/composables/useProjectRouteScope'
import { resolveStatus } from '@/design/status'
import { useProjectSetup } from './useProjectSetup'
import { setupActivationGaps, setupWorkflowReady, setupWorkflowStatus } from './viewModel'
import SetupQuestionForm from './SetupQuestionForm.vue'
import ProjectSetupLaunchDialog from './ProjectSetupLaunchDialog.vue'

const { projectId } = useProjectRouteScope()
const setup = useProjectSetup(projectId)
const { state, attempt, ready, preparationOnly, attemptStatus, neverStarted, error, writing, running, refresh } = setup
const recoveryStates = ['partial', 'failed', 'interrupted', 'cancelled']
const heading = computed(() => {
  if (state.value?.folder_status === 'unavailable') return 'Project folder unavailable'
  if (ready.value) return 'Your project is ready'
  if (attempt.value?.status === 'success' && preparationOnly.value) return 'Project preparation is complete'
  if (attemptStatus.value === 'verifying') return 'Your agent is verifying workflow readiness'
  const headings: Record<string, string> = {
    queued: 'Setup is queued', running: 'Your agent is preparing the project', needs_input: 'Your agent needs your input',
    success: 'Workflow readiness needs attention', partial: 'Setup is partly complete', failed: 'Setup could not finish',
    interrupted: 'Setup was interrupted', cancelled: 'Setup was cancelled',
  }
  return headings[attempt.value?.status ?? ''] ?? 'Project setup'
})
const pendingQuestion = computed(() => attempt.value?.question?.answer_state === 'pending'
  && !(attempt.value.entry_kind === 'existing_agent' && attempt.value.status === 'cancelled')
  && ['needs_input', ...recoveryStates].includes(attempt.value.status) ? attempt.value.question : null)
const managed = computed(() => attempt.value?.entry_kind === 'managed_ui')
const available = computed(() => state.value?.folder_status === 'available')
const retiring = computed(() => !!attempt.value?.managed?.slot_reserved)
const canContinue = computed(() => managed.value && available.value && (!!attempt.value?.thread_id || neverStarted.value) && !retiring.value && !writing.value)
const recoveryLabel = computed(() => neverStarted.value
  ? attempt.value?.status === 'failed' ? 'Retry setup' : 'Start again'
  : ({ failed: 'Retry remaining', interrupted: 'Resume setup', cancelled: 'Start again' } as Record<string, string>)[attempt.value?.status ?? ''] ?? 'Continue remaining')
const cancelTarget = ref<SchemaSetupAttempt | null>(null)
const externalWorkStopped = ref(false)
const launchOpen = ref(false)
function confirmCancellation(): void {
  if (!attempt.value) return
  cancelTarget.value = { ...attempt.value }
  externalWorkStopped.value = false
}
function closeLaunch(): void { launchOpen.value = false }
async function startedAgain(): Promise<void> {
  closeLaunch()
  await nextTick()
  stateHeading.value?.focus({ preventScroll: true })
}
const stateHeading = ref<HTMLElement | null>(null)
async function sendAnswer(snapshot: SchemaSetupAttempt, text: string): Promise<boolean> {
  const sent = await setup.answer(snapshot, text)
  await nextTick()
  if (sent && !pendingQuestion.value) stateHeading.value?.focus({ preventScroll: true })
  return sent
}
async function continueRemaining(): Promise<void> {
  if (attempt.value && await setup.continueSetup(attempt.value)) {
    await nextTick()
    stateHeading.value?.focus({ preventScroll: true })
  }
}
async function stop(): Promise<void> {
  if (!cancelTarget.value || (cancelTarget.value.entry_kind === 'existing_agent' && !externalWorkStopped.value)) return
  await setup.cancel(cancelTarget.value, externalWorkStopped.value)
  cancelTarget.value = null
  await nextTick()
  stateHeading.value?.focus({ preventScroll: true })
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}
function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && !!item.trim()) : []
}
function source(workflow: SchemaSetupWorkflowOut, applied: boolean): Record<string, unknown> {
  const sources = applied ? record(workflow.successful?.verification).sources : workflow.available_sources
  return Array.isArray(sources) ? record(sources.find(item => record(item).kind === 'template')) : {}
}
const rows = computed(() => {
  const workflows = new Map((state.value?.workflows ?? []).map(item => [item.workflow_key, item]))
  for (const key of attempt.value?.workflow_keys ?? []) {
    if (!workflows.has(key)) workflows.set(key, { workflow_key: key, status: 'unknown' } as SchemaSetupWorkflowOut)
  }
  return [...workflows.values()].map(item => {
    const template = source(item, false)
    const applied = source(item, true)
    const status = setupWorkflowStatus(item)
    const title = record(template.content).name ?? record(applied.content).name
    const details = [...strings(item.latest_result?.changes), ...strings(item.latest_result?.diagnostics), ...setupActivationGaps(item)]
    if (item.diagnostic) details.push(item.diagnostic)
    if (item.partial_changes) details.push('Files may have changed. The last verified setup is retained; it does not mean files were rolled back.')
    return {
      id: item.workflow_key, name: typeof title === 'string' ? title : item.workflow_key,
      version: typeof applied.version === 'string' ? applied.version : null,
      status,
      details,
      summary: resolveStatus('projectSetup', status).description ?? 'Setup has not been verified yet.',
    }
  })
})
type WorkflowRow = (typeof rows.value)[number]
const columns: DataTableColumn<WorkflowRow>[] = [
  { key: 'name', label: 'Workflow', widthClass: 'w-1/3', cellClass: 'max-w-0' },
  { key: 'summary', label: 'Configured result', cellClass: 'max-w-0' },
  { key: 'status', label: 'Status', widthClass: 'w-40', cellClass: 'whitespace-nowrap' },
]
const verifiedCount = computed(() => (state.value?.workflows ?? []).filter(setupWorkflowReady).length)
const questionKey = computed(() => `${attempt.value?.attempt_ref}:${attempt.value?.request_id}:${pendingQuestion.value?.question_id}:${pendingQuestion.value?.revision}`)
</script>

<template>
  <UiPageShell padded-bottom>
    <ProjectPageHeader
      :project-id="projectId"
      title="Project setup"
      :breadcrumbs="[{ label: 'Project setup' }]"
      description="Workflow configuration and setup results."
    >
      <template #actions>
        <RouterLink
          :to="`/projects/${projectId}`"
          class="focus-ring text-sm text-fg-link"
        >
          Back to Home
        </RouterLink>
        <UiButton
          size="sm"
          :loading="running"
          :disabled="writing"
          @click="refresh"
        >
          Refresh
        </UiButton>
      </template>
    </ProjectPageHeader>
    <p
      v-if="error"
      role="alert"
      class="break-words text-danger-fg"
    >
      {{ error }}
    </p>
    <template v-if="state?.eligible">
      <section
        aria-labelledby="setup-state-heading"
        class="space-y-2 break-words"
      >
        <div class="flex flex-wrap items-center gap-3">
          <h2
            id="setup-state-heading"
            ref="stateHeading"
            aria-live="polite"
            class="t-h3"
            tabindex="-1"
          >
            {{ heading }}
          </h2>
          <StatusBadge
            v-if="attempt"
            domain="projectSetup"
            :status="attemptStatus"
          />
        </div>
        <p
          v-if="state.folder_status === 'unavailable'"
          class="text-fg-muted"
        >
          Restore access to the project's bound folder, then refresh. Your setup history and reminder choices are retained.
        </p>
        <p
          v-if="state.attempt_diagnostic"
          class="text-fg-muted"
        >
          {{ state.attempt_diagnostic }}
        </p>
        <p
          v-if="ready"
          class="text-fg-muted"
        >
          Your agent will use the project's configured workflows automatically.
        </p>
        <p
          v-else-if="attempt?.status === 'success' && preparationOnly"
          class="text-fg-muted"
        >
          Preparation was requested. Workflow activation has not been verified. Return to Home to finish setup when needed.
        </p>
        <p
          v-else-if="attempt?.status === 'success'"
          class="text-fg-muted"
        >
          Completed results are retained. Review the remaining verification gaps below, then return to Home to continue setup.
        </p>
        <p
          v-if="attempt?.status === 'queued'"
          class="text-fg-muted"
        >
          Waiting for available agent capacity. This attempt will start when a slot is free.
        </p>
        <div
          v-if="attempt?.progress?.message && attempt.status !== 'success'"
          class="space-y-1"
          role="status"
        >
          <p class="text-xs font-medium text-fg-muted">Agent report</p>
          <p class="whitespace-pre-wrap [overflow-wrap:anywhere]">{{ attempt.progress.message }}</p>
        </div>
        <details
          v-if="attempt?.status === 'success' && attempt.progress?.message"
          class="text-sm"
        >
          <summary class="focus-ring w-fit cursor-pointer text-fg-link">
            Agent result
          </summary>
          <p class="mt-2 whitespace-pre-wrap text-fg-muted [overflow-wrap:anywhere]">
            {{ attempt.progress.message }}
          </p>
        </details>
        <p
          v-if="attempt?.recovery_condition"
          class="text-fg-muted"
        >
          {{ attempt.recovery_condition }}
        </p>
        <p
          v-if="attempt?.partial_changes_possible && attempt.status !== 'success'"
          class="text-fg-muted"
        >
          Before continuing, the agent will check for partial file changes. This is a precaution, not confirmation that files changed. Completed workflow results are kept; no rollback is implied.
        </p>
        <p
          v-if="attempt?.managed?.diagnostic"
          class="text-fg-muted"
        >
          Agent execution: {{ attempt.managed.diagnostic }}
        </p>
        <p
          v-if="attempt && ['queued', 'running'].includes(attempt.status)"
          class="text-xs text-fg-muted"
        >
          You can leave this page. Closing it does not stop setup.
        </p>
        <p
          v-else-if="!attempt && !ready"
          class="text-fg-muted"
        >
          No setup attempt is recorded. Return to Home to set up or update this project.
        </p>
      </section>

      <section
        v-if="rows.length"
        aria-label="Workflow setup results"
        class="space-y-2"
      >
        <p class="text-sm text-fg-muted">
          {{ verifiedCount }} of {{ rows.length }} workflows ready
        </p>
        <DataTable
          :items="rows"
          :columns="columns"
          aria-label="Workflow setup results"
          max-height="360px"
        >
          <template #cell:name="{ row }">
            <p
              class="truncate font-medium"
              :title="row.name"
            >
              {{ row.name }}
            </p>
            <p
              class="mt-1 truncate text-xs text-fg-muted"
              :title="row.version ?? 'Unknown'"
            >
              Last verified setup: {{ row.version ?? 'Unknown' }}
            </p>
          </template>
          <template #cell:summary="{ row }">
            <p
              class="truncate text-fg-muted"
              :title="row.summary"
            >
              {{ row.summary }}
            </p>
            <details
              v-if="row.details.length"
              class="mt-1 text-xs"
            >
              <summary class="focus-ring w-fit cursor-pointer text-fg-link">
                Result details
              </summary>
              <ul class="mt-2 list-disc space-y-1 pl-4 whitespace-normal [overflow-wrap:anywhere]">
                <li
                  v-for="(detail, index) in row.details"
                  :key="index"
                >
                  {{ detail }}
                </li>
              </ul>
            </details>
          </template>
          <template #cell:status="{ row }">
            <StatusBadge
              domain="projectSetup"
              :status="row.status"
            />
          </template>
        </DataTable>
        <p class="text-xs text-fg-muted">
          Setup prepares workflow guidance and verifies readiness. Your project work starts separately.
        </p>
      </section>

      <UiCard
        v-if="pendingQuestion && attempt"
        aria-label="Agent question"
      >
        <template #header>
          <h2 class="t-h3">
            Agent question
          </h2>
        </template>
        <div class="space-y-3">
          <p class="whitespace-pre-wrap font-medium [overflow-wrap:anywhere]">
            {{ pendingQuestion.prompt }}
          </p>
          <p
            v-if="!managed || pendingQuestion.response_channel === 'existing_agent'"
            class="text-fg-muted"
          >
            Answer in the agent session where you started setup.
          </p>
          <template v-else>
            <p
              v-if="retiring"
              class="text-xs text-fg-muted"
            >
              The agent is finishing its previous turn. Check again before sending.
            </p>
            <p
              v-else-if="!attempt.thread_id"
              class="text-xs text-fg-muted"
            >
              The original agent session is unavailable. Refresh to check recovery; an answer cannot start a replacement session.
            </p>
            <SetupQuestionForm
              :key="questionKey"
              :attempt="attempt"
              :pending="writing"
              :disabled="!canContinue"
              :submit="sendAnswer"
            />
          </template>
        </div>
      </UiCard>

      <div
        v-if="attempt"
        class="flex flex-wrap items-center justify-between gap-3"
      >
        <p
          v-if="recoveryStates.includes(attempt.status)"
          class="max-w-2xl text-sm text-fg-muted"
        >
          {{ neverStarted ? 'Setup stopped before the agent started. Try again with the same agent settings; completed workflow results are kept.' : managed ? 'Continue only remaining work. The agent will recheck completed workflows if their guidance has changed.' : attempt.status === 'cancelled' ? 'Completed results are kept. Files may already have changed. Set up again when you are ready; the agent will check the current guidance.' : 'Continue in the agent session where you started setup.' }}
        </p>
        <div class="ml-auto flex flex-wrap gap-2">
          <UiButton
            v-if="['queued', 'running', 'needs_input'].includes(attempt.status) && !attempt.managed?.cancel_requested"
            :disabled="writing"
            @click="confirmCancellation"
          >
            Cancel setup
          </UiButton>
          <UiButton
            v-if="!managed && attempt.status === 'cancelled'"
            variant="primary"
            :disabled="writing || !available"
            @click="launchOpen = true"
          >
            Set up again
          </UiButton>
          <UiButton
            v-if="managed && recoveryStates.includes(attempt.status) && !pendingQuestion"
            variant="primary"
            :disabled="!canContinue"
            :loading="writing"
            @click="continueRemaining"
          >
            {{ recoveryLabel }}
          </UiButton>
        </div>
        <p
          v-if="managed && recoveryStates.includes(attempt.status) && !pendingQuestion && !canContinue && !writing"
          class="w-full text-xs text-fg-muted"
        >
          {{ retiring ? 'Waiting for the previous invocation to retire. Refresh before continuing.' : !available ? 'Restore access to the project folder before continuing.' : 'Exact agent-session recovery is unavailable. Refresh to check the recorded recovery condition.' }}
        </p>
      </div>
    </template>
    <p
      v-else-if="state"
      class="text-fg-muted"
    >
      Repository setup is available for projects with a bound folder.
    </p>
    <p
      v-else-if="!error"
      role="status"
      class="text-fg-muted"
    >
      Loading project setup…
    </p>
    <ProjectSetupLaunchDialog
      v-if="launchOpen && state"
      :state="state"
      :pending="writing"
      :error="error"
      :start="setup.start"
      @close="closeLaunch"
      @started="startedAgain"
    />
    <UiDialog
      :model-value="!!cancelTarget"
      title="Stop project setup?"
      described-by="setup-cancel-description"
      size="sm"
      @update:model-value="value => { if (!value && !writing) cancelTarget = null }"
    >
      <div class="space-y-3">
        <p id="setup-cancel-description">
          Completed workflow results are kept. Files may already have changed; stopping setup does not roll them back.
        </p>
        <template v-if="cancelTarget?.entry_kind === 'existing_agent'">
          <p>Stop the previous setup work in your agent app first. StackOS cannot stop that agent or verify that it has exited.</p>
          <UiCheckbox
            v-model="externalWorkStopped"
            :disabled="writing"
            label="I stopped the previous setup work in my agent app."
          />
        </template>
      </div>
      <template #footer>
        <UiButton
          variant="ghost"
          :disabled="writing"
          @click="cancelTarget = null"
        >
          Keep working
        </UiButton>
        <UiButton
          variant="primary"
          :loading="writing"
          :disabled="writing || (cancelTarget?.entry_kind === 'existing_agent' && !externalWorkStopped)"
          @click="stop"
        >
          Stop setup
        </UiButton>
      </template>
    </UiDialog>
  </UiPageShell>
</template>
