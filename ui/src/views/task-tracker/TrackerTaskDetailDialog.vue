<script setup lang="ts">
import { computed, nextTick, reactive, ref, toRef } from 'vue'

import {
  UiBadge,
  UiButton,
  UiCallout,
  UiDialog,
  UiDescriptionList,
  UiEmptyState,
  UiJsonBlock,
  UiMetadataStrip,
  UiLoadingState,
  UiFormField,
  UiTextarea,
} from '@/components/ui'
import StatusBadge from '@/components/StatusBadge.vue'
import { formatDateTime, sanitizeForDisplay } from '@/lib/stackos/json'
import type { TrackerTask, TrackerTicket } from '@/lib/task-tracker/types'
import { isGraphBlockedTicket } from './viewModel'
import { useTrackerTaskContent } from './useTrackerTaskContent'
import TrackerTicketDetailContent from './TrackerTicketDetailContent.vue'

import type {
  TaskExecutionContext,
  TaskExecutionContextArtifact,
  TaskExecutionContextArtifactPageInfo,
  TaskExecutionContextPageInfo,
} from './executionContextTypes'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    task: TrackerTask | null
    tickets?: TrackerTicket[]
    loading?: boolean
    error?: string | null
    ticketKey?: string
    ticket?: TrackerTicket | null
    ticketError?: string | null
    canGoBack?: boolean
    refreshContent?: ((taskKey: string) => Promise<TrackerTask>) | null
    contexts?: TaskExecutionContext[]
    contextArtifacts?: Record<string, TaskExecutionContextArtifact[]>
    contextPageInfo?: TaskExecutionContextPageInfo | null
    contextArtifactPageInfo?: TaskExecutionContextArtifactPageInfo
    contextLoading?: boolean
    contextError?: string | null
  }>(),
  {
    contexts: () => [],
    tickets: () => [],
    loading: false,
    error: null,
    ticketKey: '',
    ticket: null,
    ticketError: null,
    canGoBack: true,
    refreshContent: null,
    contextArtifacts: () => ({}),
    contextPageInfo: null,
    contextArtifactPageInfo: () => ({}),
    contextLoading: false,
    contextError: null,
  },
)

defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'viewDependencies', taskKey: string): void
  (e: 'openTicket', ticket: TrackerTicket): void
  (e: 'retry'): void
  (e: 'backToTask'): void
}>()

const content = reactive(useTrackerTaskContent({
  task: toRef(props, 'task'),
  refresh: async (taskKey) => {
    if (!props.refreshContent) throw new Error('Content editing is unavailable.')
    return props.refreshContent(taskKey)
  },
}))
const frame = ref<{ body: HTMLElement | null; focusTitle: () => void } | null>(null)
const taskPane = ref<HTMLElement | null>(null)
const feedback = ref<HTMLElement | null>(null)
let taskScroll = 0
let returnFocus: HTMLElement | null = null
let currentTaskKey = props.task?.key ?? ''

function syncTask(task: TrackerTask | null): void {
  if ((task?.key ?? '') !== currentTaskKey) {
    currentTaskKey = task?.key ?? ''
    taskScroll = 0
    returnFocus = null
    if (frame.value?.body) frame.value.body.scrollTop = 0
  }
  content.syncTask(task)
}

function prepareInspection(nextTicketKey: string): void {
  if (!props.ticketKey && nextTicketKey) {
    taskScroll = frame.value?.body?.scrollTop ?? 0
    returnFocus = taskPane.value?.contains(document.activeElement) ? document.activeElement as HTMLElement : null
  }
}

async function enterTicket(): Promise<void> {
  // Let the dialog's initial focus lifecycle finish before focusing this inspection.
  await nextTick()
  await nextTick()
  if (!props.modelValue || !props.ticketKey) return
  if (frame.value?.body) frame.value.body.scrollTop = 0
  frame.value?.focusTitle()
}

async function returnToTask(): Promise<void> {
  await nextTick()
  if (!props.modelValue || props.ticketKey) return
  if (frame.value?.body) frame.value.body.scrollTop = taskScroll
  if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true })
  else frame.value?.focusTitle()
}

async function beginEdit(): Promise<void> {
  content.beginEdit()
  await nextTick()
  taskPane.value?.querySelector<HTMLTextAreaElement>('[data-description-editor]')?.focus()
}

async function cancelEdit(): Promise<void> {
  content.cancelEdit()
  await nextTick()
  if (!content.editing) taskPane.value?.querySelector<HTMLElement>('[data-edit-content]')?.focus({ preventScroll: true })
}

const saveLabel = computed(() => {
  if (content.pendingIntent && !content.saving) return 'Retry save'
  const hasDescription = content.pendingIntent ? Object.hasOwn(content.pendingIntent, 'description') : content.descriptionChanged
  const hasInput = content.pendingIntent ? Boolean(content.pendingIntent.additional_input) : Boolean(content.additionalInput.trim())
  if (hasDescription && hasInput) return 'Save description and input'
  return hasDescription ? 'Save description' : 'Add input'
})
const feedbackMessage = computed(() => content.saving ? content.error || 'Saving…'
  : content.refreshing ? 'Loading latest content…' : content.error || content.notice)

async function runContentAction(action: () => Promise<void>): Promise<void> {
  const task = props.task
  if (!task) return
  const focusFeedback = () => {
    if (props.modelValue && !props.ticketKey && props.task?.key === task.key && props.task.project_id === task.project_id) {
      feedback.value?.focus({ preventScroll: true })
    }
  }
  const request = action()
  await nextTick()
  focusFeedback()
  await request
  await nextTick()
  focusFeedback()
}

defineExpose({ confirmLeave: content.confirmLeave, syncTask, prepareInspection })

const operatorInputs = computed(() => {
  const items = props.task?.context_json?.operator_inputs
  if (!Array.isArray(items)) return []
  return items.filter((item): item is { id: string; text: string; created_at: string; actor: string } =>
    item && typeof item === 'object' && typeof item.id === 'string'
    && typeof item.text === 'string' && typeof item.created_at === 'string',
  )
})
const completedCount = computed(() => props.tickets.filter((ticket) => ticket.status === 'complete').length)

const taskFacts = computed(() => {
  const task = props.task
  if (!task) return []
  return [
    { label: 'Owner', value: task.owner ?? '-' },
    { label: 'Priority', value: task.priority_key, mono: true },
    { label: 'Lane', value: task.lane_key, mono: true },
    { label: 'Type', value: task.task_type, mono: true },
    { label: 'Source', value: task.source_kind, mono: true },
    { label: 'Updated', value: formatDateTime(task.updated_at) },
  ]
})

const taskTraceFacts = computed(() => {
  const task = props.task
  if (!task) return []
  return [
    { label: 'Created', value: formatDateTime(task.created_at) },
    { label: 'Started', value: task.started_at ? formatDateTime(task.started_at) : '-' },
    { label: 'Completed', value: task.completed_at ? formatDateTime(task.completed_at) : '-' },
  ]
})

const workflowManaged = computed(() => {
  const task = props.task
  if (!task) return false
  return task.source_kind === 'workflow' || typeof task.source_json?.run_plan_id === 'number'
})

const hasTaskJson = computed(() => {
  const task = props.task
  if (!task) return false
  return [
    task.completion_evidence_json,
    task.source_json,
    task.context_json,
    task.metadata_json,
  ].some(hasJsonObject)
})

function hasJsonObject(value: Record<string, unknown> | null): boolean {
  return Boolean(value && Object.keys(value).length > 0)
}

const hiddenContextCount = computed(() => {
  const total = props.contextPageInfo?.totalEstimate ?? props.contexts.length
  return Math.max(0, total - props.contexts.length)
})

function hiddenArtifactCount(contextRef: string): number {
  const pageInfo = props.contextArtifactPageInfo[contextRef]
  const visible = (props.contextArtifacts[contextRef] ?? []).length
  const total = pageInfo?.totalEstimate ?? visible
  return Math.max(0, total - visible)
}

function contextScopeLabel(context: TaskExecutionContext): string {
  return [context.plugin_slug, context.provider_key].filter(Boolean).join(' / ') || 'generic'
}

function compactJson(value: Record<string, unknown> | undefined | null): string {
  if (!value || Object.keys(value).length === 0) return '-'
  return JSON.stringify(sanitizeForDisplay(value))
}

function artifactName(item: TaskExecutionContextArtifact): string {
  const artifactName = item.artifact.name
  const artifactUri = item.artifact.uri
  if (item.semantic_name) return item.semantic_name
  if (typeof artifactName === 'string' && artifactName) return artifactName
  if (typeof artifactUri === 'string' && artifactUri) return artifactUri
  return `Artifact #${item.artifact_id}`
}

function artifactDetail(item: TaskExecutionContextArtifact): string {
  const metadata = item.metadata_json ?? {}
  const artifact = item.artifact
  const bytes = metadata.bytes ?? artifact.size_bytes
  const contentType = metadata.content_type ?? artifact.mime_type
  return [contentType, bytes ? `${bytes} bytes` : null, item.action_ref].filter(Boolean).join(' · ')
}

function artifactPath(item: TaskExecutionContextArtifact): string {
  const metadataPath = item.metadata_json?.absolute_path
  if (typeof metadataPath === 'string' && metadataPath) return metadataPath
  const uri = item.artifact.uri
  return typeof uri === 'string' ? uri : ''
}
</script>

<template>
  <UiDialog
    ref="frame"
    :model-value="modelValue"
    :title="ticketKey ? ticket?.title ?? 'Ticket details' : task?.title ?? 'Task detail'"
    :description="ticketKey ? ticketKey : task ? `Task #${task.id}` : undefined"
    size="lg"
    layout="detail"
    scroll-body
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <div
      :key="task?.key ?? 'loading'"
      ref="taskPane"
      :hidden="Boolean(ticketKey)"
      :inert="Boolean(ticketKey)"
      class="tracker-task-detail min-w-0"
    >
      <UiLoadingState
        v-if="loading && !task"
        label="Loading task details…"
      />
      <UiCallout
        v-if="error"
        tone="danger"
      >
        {{ error }}
        <UiButton
          variant="ghost"
          size="sm"
          @click="$emit('retry')"
        >
          Retry
        </UiButton>
      </UiCallout>
      <div
        v-if="task"
        class="space-y-5"
      >
        <div class="flex flex-wrap items-center gap-3 text-sm text-fg-muted">
          <StatusBadge
            domain="tracker"
            :status="task.status"
          />
          <span>Owner <span class="text-fg-default">{{ task.owner ?? 'Unassigned' }}</span></span>
          <span>{{ completedCount }} of {{ tickets.length }} tickets complete</span>
        </div>
        <section class="space-y-2 border-t border-subtle pt-4">
          <div class="flex items-center justify-between gap-3">
            <h3 class="t-h3 text-fg-strong">
              Description
            </h3>
            <UiButton
              v-if="refreshContent && !content.editing"
              variant="secondary"
              size="sm"
              icon-left="edit"
              data-edit-content
              :disabled="content.locked"
              @click="beginEdit"
            >
              Edit description
            </UiButton>
            <UiButton
              v-else-if="content.editing"
              variant="ghost"
              size="sm"
              :disabled="content.locked"
              @click="cancelEdit"
            >
              Cancel edit
            </UiButton>
          </div>
          <UiFormField
            v-if="content.editing"
            label="Description"
            hide-label
            :dirty="content.description !== task.description"
          >
            <UiTextarea
              v-model="content.description"
              data-description-editor
              resize="none"
              :rows="4"
              :maxlength="20000"
              :readonly="content.locked"
            />
          </UiFormField>
          <p
            v-else
            class="whitespace-pre-wrap break-words text-sm leading-5 text-fg-default"
          >
            {{ task.description || 'No description yet.' }}
          </p>
          <UiCallout
            v-if="content.latestDescription !== null"
            tone="neutral"
            density="compact"
          >
            <p class="font-medium">
              Latest saved description
            </p>
            <p class="mt-1 whitespace-pre-wrap break-words">
              {{ content.latestDescription || 'No description.' }}
            </p>
          </UiCallout>
        </section>
        <section class="space-y-3 border-t border-subtle pt-4">
          <div class="flex items-center justify-between gap-3">
            <h3 class="t-h3 text-fg-strong">
              Tickets <span class="ml-2 text-xs font-normal text-fg-muted">{{ completedCount }} of {{ tickets.length }} complete</span>
            </h3>
            <UiButton
              variant="secondary"
              size="sm"
              icon-left="git-branch"
              @click="$emit('viewDependencies', task.key)"
            >
              View graph
            </UiButton>
          </div>
          <div
            v-if="tickets.length"
            class="overflow-hidden rounded-lg border border-subtle"
          >
            <div
              class="tracker-task-ticket-list focus-ring max-h-60 overflow-y-auto"
              role="region"
              aria-label="Task tickets"
              tabindex="0"
            >
              <button
                v-for="row in tickets"
                :key="row.key"
                type="button"
                class="focus-ring flex h-10 w-full items-center gap-3 border-b border-subtle px-3 text-left text-sm last:border-b-0 hover:bg-bg-surface-alt"
                @click="$emit('openTicket', row)"
              >
                <span class="shrink-0 font-mono text-xs text-fg-muted">#{{ row.id }}</span>
                <span
                  class="min-w-0 flex-1 truncate text-fg-default"
                  :title="row.title"
                >{{ row.title }}</span>
                <UiBadge
                  v-if="isGraphBlockedTicket(row)"
                  tone="warning"
                >
                  Blocked
                </UiBadge>
                <StatusBadge
                  class="shrink-0"
                  domain="tracker"
                  :status="row.status"
                />
              </button>
            </div>
          </div>
          <p
            v-else
            class="text-sm text-fg-muted"
          >
            No tickets yet.
          </p>
        </section>
        <section
          v-if="refreshContent || operatorInputs.length"
          class="space-y-3 border-t border-subtle pt-4"
        >
          <h3 class="t-h3 text-fg-strong">
            Additional input
          </h3>
          <ol
            v-if="operatorInputs.length"
            class="space-y-3"
            aria-label="Additional input history"
          >
            <li
              v-for="input in operatorInputs"
              :key="input.id"
              class="rounded-lg border border-subtle bg-bg-surface-alt px-3 py-2"
            >
              <p class="text-xs text-fg-muted">
                Operator · {{ formatDateTime(input.created_at) }}
              </p>
              <p class="mt-1 whitespace-pre-wrap break-words text-sm leading-5 text-fg-default">
                {{ input.text }}
              </p>
            </li>
          </ol>
          <UiFormField
            v-if="refreshContent"
            label="Add context"
          >
            <UiTextarea
              v-model="content.additionalInput"
              resize="none"
              :rows="3"
              :maxlength="20000"
              :readonly="content.locked"
              placeholder="Write a clarification or add a link…"
            />
          </UiFormField>
        </section>
        <details class="border-t border-subtle pt-4">
          <summary class="focus-ring cursor-pointer rounded-xs text-sm font-medium text-fg-muted">
            More details
          </summary>
          <div class="mt-4 space-y-5">
            <section class="space-y-3 rounded-lg border border-subtle bg-bg-surface-alt px-3 py-3">
              <div class="flex flex-wrap items-start justify-between gap-3">
                <div class="min-w-0">
                  <h3 class="t-h3 text-fg-strong">
                    Overview
                  </h3>
                  <div class="mt-1.5 flex flex-wrap items-center gap-2">
                    <StatusBadge
                      domain="tracker"
                      :status="task.status"
                    />
                    <UiBadge
                      v-if="workflowManaged"
                      tone="accent"
                    >
                      Run plan linked
                    </UiBadge>
                  </div>
                </div>
              </div>
              <UiMetadataStrip
                :items="taskFacts"
                aria-label="Task core metadata"
              />
              <details class="rounded-sm border border-subtle bg-bg-surface px-2.5 py-1.5">
                <summary class="focus-ring cursor-pointer rounded-xs text-xs font-medium text-fg-muted">
                  Trace
                </summary>
                <UiMetadataStrip
                  class="mt-2"
                  :items="taskTraceFacts"
                  aria-label="Task trace metadata"
                />
              </details>
              <p class="text-xs text-fg-muted">
                Status is managed by the originating agent or run-plan lifecycle.
              </p>
            </section>

            <section
              v-if="task.goal"
              class="space-y-2"
            >
              <h3 class="t-h3 text-fg-strong">
                Goal
              </h3>
              <p class="max-w-[76ch] text-sm leading-6 text-fg-default">
                {{ task.goal }}
              </p>
            </section>

            <section
              v-if="task.definition_of_done_json.length"
              class="space-y-2 border-t border-subtle pt-4"
            >
              <h3 class="t-h3 text-fg-strong">
                Definition of done
              </h3>
              <ul class="grid gap-1.5 text-sm leading-5 text-fg-default">
                <li
                  v-for="item in task.definition_of_done_json"
                  :key="item"
                  class="rounded-sm bg-bg-surface-alt px-2 py-1"
                >
                  {{ item }}
                </li>
              </ul>
            </section>

            <section
              v-if="task.expected_outcomes_json.length || task.constraints_json.length"
              class="grid gap-4 border-t border-subtle pt-4 md:grid-cols-2"
            >
              <div
                v-if="task.expected_outcomes_json.length"
                class="space-y-2"
              >
                <h3 class="t-h3 text-fg-strong">
                  Expected outcomes
                </h3>
                <ul class="grid gap-1.5 text-sm leading-5 text-fg-default">
                  <li
                    v-for="item in task.expected_outcomes_json"
                    :key="item"
                    class="rounded-sm bg-bg-surface-alt px-2 py-1"
                  >
                    {{ item }}
                  </li>
                </ul>
              </div>
              <div
                v-if="task.constraints_json.length"
                class="space-y-2"
              >
                <h3 class="t-h3 text-fg-strong">
                  Constraints
                </h3>
                <ul class="grid gap-1.5 text-sm leading-5 text-fg-default">
                  <li
                    v-for="item in task.constraints_json"
                    :key="item"
                    class="rounded-sm bg-bg-surface-alt px-2 py-1"
                  >
                    {{ item }}
                  </li>
                </ul>
              </div>
            </section>

            <section class="space-y-3 border-t border-subtle pt-4">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h3 class="t-h3 text-fg-strong">
                    Task context
                  </h3>
                  <p class="mt-0.5 text-sm text-fg-muted">
                    Context refs and file-backed outputs linked directly to this task.
                  </p>
                </div>
                <UiBadge>
                  {{ contextPageInfo?.totalEstimate ?? contexts.length }} contexts
                </UiBadge>
              </div>

              <UiCallout
                v-if="contextError"
                tone="warning"
                density="compact"
              >
                {{ contextError }}
              </UiCallout>

              <p
                v-if="contextLoading"
                class="rounded-lg border border-dashed border-subtle bg-bg-surface-alt px-4 py-5 text-sm text-fg-muted"
              >
                Loading task contexts...
              </p>
              <UiEmptyState
                v-else-if="contexts.length === 0"
                title="No task contexts linked"
                description="Agents can link context_ref values to this task so credentials, provider scope, and files are easy to reuse."
                size="sm"
              />
              <div
                v-else
                class="grid gap-3"
              >
                <UiCallout
                  v-if="hiddenContextCount > 0"
                  tone="neutral"
                  density="compact"
                >
                  Showing first {{ contexts.length }} of {{ contextPageInfo?.totalEstimate }} contexts.
                </UiCallout>
                <article
                  v-for="context in contexts"
                  :key="context.context_ref"
                  class="rounded-lg border border-subtle bg-bg-surface px-3 py-3"
                >
                  <div class="flex flex-wrap items-start justify-between gap-3">
                    <div class="min-w-0">
                      <p class="truncate text-sm font-semibold text-fg-strong">
                        {{ context.name }}
                      </p>
                      <p class="truncate font-mono text-xs text-fg-muted">
                        {{ context.context_ref }}
                      </p>
                    </div>
                    <span class="flex flex-wrap justify-end gap-1">
                      <UiBadge tone="accent">{{ contextScopeLabel(context) }}</UiBadge>
                      <UiBadge>{{ context.status }}</UiBadge>
                    </span>
                  </div>
                  <p
                    v-if="context.description"
                    class="mt-2 text-sm text-fg-muted"
                  >
                    {{ context.description }}
                  </p>
                  <UiDescriptionList
                    class="mt-3"
                    layout="grid"
                    :columns="2"
                    density="compact"
                    :items="[
                      { label: 'Action', value: context.action_ref ?? '-', mono: true },
                      { label: 'Credential', value: context.credential_ref ?? '-', mono: true },
                      { label: 'Namespace', value: context.artifact_namespace ?? '-', mono: true },
                      { label: 'Artifacts', value: context.artifact_count ?? 0 },
                      { label: 'Output', value: compactJson(context.output_policy_json), mono: true },
                      { label: 'Budget', value: compactJson(context.request_budget_json), mono: true },
                    ]"
                  />

                  <div
                    v-if="contextArtifacts[context.context_ref]?.length"
                    class="mt-3 space-y-2 border-t border-subtle pt-3"
                  >
                    <p class="text-xs font-medium text-fg-muted">
                      Files
                    </p>
                    <ul class="grid gap-2">
                      <li
                        v-for="artifact in contextArtifacts[context.context_ref] ?? []"
                        :key="artifact.id"
                        class="rounded-sm border border-subtle bg-bg-surface-alt px-3 py-2"
                      >
                        <div class="flex flex-wrap items-start justify-between gap-2">
                          <div class="min-w-0">
                            <p class="truncate text-sm font-medium text-fg-strong">
                              {{ artifactName(artifact) }}
                            </p>
                            <p class="mt-1 text-xs text-fg-muted">
                              {{ artifactDetail(artifact) }}
                            </p>
                          </div>
                          <UiBadge v-if="artifact.action_call_id">
                            Call #{{ artifact.action_call_id }}
                          </UiBadge>
                        </div>
                        <p
                          v-if="artifactPath(artifact)"
                          class="mt-1 truncate font-mono text-xs text-fg-muted"
                        >
                          {{ artifactPath(artifact) }}
                        </p>
                      </li>
                    </ul>
                    <p
                      v-if="hiddenArtifactCount(context.context_ref) > 0"
                      class="text-xs text-fg-muted"
                    >
                      Showing first {{ contextArtifacts[context.context_ref]?.length ?? 0 }} of
                      {{ contextArtifactPageInfo[context.context_ref]?.totalEstimate }} files.
                    </p>
                  </div>
                </article>
              </div>
            </section>

            <details
              v-if="hasTaskJson"
              class="rounded-lg border border-subtle bg-bg-surface px-3 py-2"
            >
              <summary class="focus-ring cursor-pointer rounded-xs text-xs font-medium text-fg-muted">
                Raw metadata
              </summary>
              <div class="mt-3 space-y-4">
                <div
                  v-if="hasJsonObject(task.completion_evidence_json)"
                  class="space-y-2"
                >
                  <p class="text-xs font-medium text-fg-muted">
                    Completion evidence
                  </p>
                  <UiJsonBlock
                    :data="sanitizeForDisplay(task.completion_evidence_json)"
                    density="compact"
                    max-height="16rem"
                    wrap
                  />
                </div>
                <div
                  v-if="hasJsonObject(task.source_json)"
                  class="space-y-2"
                >
                  <p class="text-xs font-medium text-fg-muted">
                    Source
                  </p>
                  <UiJsonBlock
                    :data="sanitizeForDisplay(task.source_json)"
                    density="compact"
                    max-height="16rem"
                    wrap
                  />
                </div>
                <div
                  v-if="hasJsonObject(task.context_json)"
                  class="space-y-2"
                >
                  <p class="text-xs font-medium text-fg-muted">
                    Context
                  </p>
                  <UiJsonBlock
                    :data="sanitizeForDisplay(task.context_json)"
                    density="compact"
                    max-height="16rem"
                    wrap
                  />
                </div>
                <div
                  v-if="hasJsonObject(task.metadata_json)"
                  class="space-y-2"
                >
                  <p class="text-xs font-medium text-fg-muted">
                    Metadata
                  </p>
                  <UiJsonBlock
                    :data="sanitizeForDisplay(task.metadata_json)"
                    density="compact"
                    max-height="16rem"
                    wrap
                  />
                </div>
              </div>
            </details>
          </div>
        </details>
      </div>
    </div>
    <TrackerTicketDetailContent
      v-if="ticketKey"
      :key="ticketKey"
      :ticket="ticket"
      :loading="loading"
      :error="ticketError"
      @vue:mounted="enterTicket"
      @vue:unmounted="returnToTask"
    />
    <template #footer>
      <div
        v-if="feedbackMessage"
        ref="feedback"
        tabindex="-1"
        class="w-full min-w-0 text-left [overflow-wrap:anywhere]"
        data-content-feedback
      >
        <UiCallout
          v-if="content.error && !content.refreshing"
          tone="warning"
          density="compact"
          role="alert"
        >
          {{ feedbackMessage }}
          <UiButton
            v-if="content.conflict"
            variant="secondary"
            size="sm"
            @click="runContentAction(content.refreshLatest)"
          >
            Load latest content
          </UiButton>
        </UiCallout>
        <p
          v-else
          role="status"
          class="text-sm text-fg-muted"
        >
          {{ feedbackMessage }}
        </p>
      </div>
      <UiButton
        v-if="ticketKey && canGoBack"
        variant="ghost"
        size="sm"
        class="mr-auto"
        @click="$emit('backToTask')"
      >
        Back to task
      </UiButton>
      <p
        v-if="!ticketKey"
        class="mr-auto text-xs text-fg-muted"
      >
        Status is managed by agents.
      </p>
      <UiButton
        variant="secondary"
        size="sm"
        @click="$emit('update:modelValue', false)"
      >
        Close
      </UiButton>
      <UiButton
        v-if="!ticketKey && task && refreshContent"
        size="sm"
        :disabled="!content.canSave"
        :loading="content.saving"
        @click="runContentAction(content.save)"
      >
        {{ saveLabel }}
      </UiButton>
    </template>
  </UiDialog>
</template>
