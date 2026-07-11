<script setup lang="ts">
import { computed } from 'vue'

import InspectableDetailDrawer from '@/components/InspectableDetailDrawer.vue'
import {
  UiBadge,
  UiCallout,
  UiDescriptionList,
  UiEmptyState,
  UiJsonBlock,
  UiMetadataStrip,
} from '@/components/ui'
import StatusBadge from '@/components/StatusBadge.vue'
import { formatDateTime, sanitizeForDisplay } from '@/lib/stackos/json'
import type { TrackerTask } from '@/lib/task-tracker/types'

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
    contexts?: TaskExecutionContext[]
    contextArtifacts?: Record<string, TaskExecutionContextArtifact[]>
    contextPageInfo?: TaskExecutionContextPageInfo | null
    contextArtifactPageInfo?: TaskExecutionContextArtifactPageInfo
    contextLoading?: boolean
    contextError?: string | null
  }>(),
  {
    contexts: () => [],
    contextArtifacts: () => ({}),
    contextPageInfo: null,
    contextArtifactPageInfo: () => ({}),
    contextLoading: false,
    contextError: null,
  },
)

defineEmits<{
  (e: 'update:modelValue', value: boolean): void
}>()

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
  <InspectableDetailDrawer
    :model-value="modelValue"
    :title="task?.title ?? 'Task detail'"
    :description="task?.key"
    size="lg"
    :has-detail="Boolean(task)"
    empty-title="No task selected"
    empty-description="Select a task to inspect its details."
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <div
      v-if="task"
      class="space-y-5"
    >
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
        v-if="task.goal || task.description"
        class="space-y-2"
      >
        <h3 class="t-h3 text-fg-strong">
          Summary
        </h3>
        <p class="max-w-[76ch] text-sm leading-6 text-fg-default">
          {{ task.goal || task.description }}
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
  </InspectableDetailDrawer>
</template>
