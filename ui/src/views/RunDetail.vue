<script setup lang="ts">
// RunDetail — single-run audit view with run plans, children, and linked data.

import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import ArtifactRenderer from '@/components/renderers/ArtifactRenderer.vue'
import KvList from '@/components/KvList.vue'
import RunPlanRenderer from '@/components/renderers/RunPlanRenderer.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import {
  UiAdvancedJsonPanel,
  UiBadge,
  UiCard,
  UiEmptyState,
  UiJsonBlock,
  UiSectionHeader,
} from '@/components/ui'
import { useRunsStore, type Run } from '@/stores/runs'
import { useToastsStore } from '@/stores/toasts'
import { apiFetch, formatApiError } from '@/lib/client'
import { resolveStatus } from '@/design/status'
import { formatDateTime, sanitizeForDisplay } from '@/lib/stackos/json'
import { newestFirst } from '@/lib/stackos/time'
import type {
  SchemaActionCallAuditOut,
  SchemaArtifactOut,
  SchemaContextSnapshotOut,
  SchemaDecisionOut,
  SchemaExperimentObservationOut,
  SchemaExperimentOut,
  SchemaLearningOut,
  SchemaPageResponseActionCallAuditOut,
  SchemaPageResponseArtifactOut,
  SchemaPageResponseContextSnapshotOut,
  SchemaPageResponseDecisionOut,
  SchemaPageResponseExperimentObservationOut,
  SchemaPageResponseExperimentOut,
  SchemaPageResponseLearningOut,
  SchemaPageResponseRunPlanSummaryOut,
  SchemaRunPlanOut,
} from '@/api'

const props = defineProps<{
  runId: number
  projectId: number
}>()

const runsStore = useRunsStore()
const toasts = useToastsStore()

const run = ref<Run | null>(null)
const children = ref<Run[]>([])
const runPlans = ref<SchemaRunPlanOut[]>([])
const actionCalls = ref<SchemaActionCallAuditOut[]>([])
const contextSnapshots = ref<SchemaContextSnapshotOut[]>([])
const observations = ref<SchemaExperimentObservationOut[]>([])
const decisions = ref<SchemaDecisionOut[]>([])
const learnings = ref<SchemaLearningOut[]>([])
const experiments = ref<SchemaExperimentOut[]>([])
const artifacts = ref<SchemaArtifactOut[]>([])
const loading = ref(false)

async function load(): Promise<void> {
  loading.value = true
  try {
    run.value = await runsStore.get(props.runId)
    const [
      kids,
      planRows,
      actionCallPage,
      snapshotPage,
      observationPage,
      decisionPage,
      learningPage,
      experimentPage,
      artifactPage,
    ] = await Promise.all([
      runsStore.children(props.runId),
      fetchRunPlans(),
      apiFetch<SchemaPageResponseActionCallAuditOut>(
        `/api/v1/projects/${props.projectId}/action-calls?run_id=${props.runId}&limit=50`,
      ),
      apiFetch<SchemaPageResponseContextSnapshotOut>(
        `/api/v1/projects/${props.projectId}/context/snapshots?run_id=${props.runId}&limit=50`,
      ),
      apiFetch<SchemaPageResponseExperimentObservationOut>(
        `/api/v1/projects/${props.projectId}/experiments/observations?run_id=${props.runId}&limit=50`,
      ),
      apiFetch<SchemaPageResponseDecisionOut>(
        `/api/v1/projects/${props.projectId}/decisions?run_id=${props.runId}&limit=50`,
      ),
      apiFetch<SchemaPageResponseLearningOut>(
        `/api/v1/projects/${props.projectId}/learnings?limit=50`,
      ),
      apiFetch<SchemaPageResponseExperimentOut>(
        `/api/v1/projects/${props.projectId}/experiments?limit=50`,
      ),
      apiFetch<SchemaPageResponseArtifactOut>(
        `/api/v1/projects/${props.projectId}/artifacts?limit=50`,
      ),
    ])
    // The API returns rows ascending — show the latest first in every section.
    children.value = newestFirst(kids, (row) => row.started_at)
    runPlans.value = newestFirst(planRows, (row) => row.created_at)
    actionCalls.value = newestFirst(actionCallPage.items, (row) => row.created_at)
    contextSnapshots.value = newestFirst(snapshotPage.items, (row) => row.created_at)
    observations.value = newestFirst(
      observationPage.items,
      (row) => row.observed_at ?? row.created_at,
    )
    decisions.value = newestFirst(decisionPage.items, (row) => row.created_at)
    learnings.value = newestFirst(learningPage.items, (row) => row.created_at)
    experiments.value = newestFirst(experimentPage.items, (row) => row.created_at)
    artifacts.value = newestFirst(artifactPage.items, (row) => row.created_at)
  } catch (err) {
    toasts.error('Failed to load run', formatApiError(err))
  } finally {
    loading.value = false
  }
}

interface KvItem {
  key: string
  label: string
  value: unknown
}

const summary = computed<KvItem[]>(() => {
  if (!run.value) return []
  return [
    { key: 'id', label: 'Run id', value: `#${run.value.id}` },
    { key: 'kind', label: 'Kind', value: run.value.kind },
    { key: 'status', label: 'Status', value: resolveStatus('run', run.value.status).label },
    { key: 'started', label: 'Started', value: new Date(run.value.started_at).toLocaleString() },
    {
      key: 'ended',
      label: 'Ended',
      value: run.value.ended_at ? new Date(run.value.ended_at).toLocaleString() : '—',
    },
    { key: 'duration', label: 'Duration', value: durationOf(run.value) },
    {
      key: 'parent',
      label: 'Parent',
      value: run.value.parent_run_id !== null ? `#${run.value.parent_run_id}` : '—',
    },
    { key: 'last_step', label: 'Last step', value: run.value.last_step ?? '—' },
    { key: 'error', label: 'Error', value: run.value.error ?? '—' },
  ]
})

const hasMetadata = computed<boolean>(() => {
  const meta = run.value?.metadata_json
  return !!meta && typeof meta === 'object' && Object.keys(meta).length > 0
})

const linkedSnapshotIds = computed<Set<number>>(
  () => new Set(contextSnapshots.value.map((snapshot) => snapshot.id)),
)

const linkedLearnings = computed(() =>
  learnings.value.filter(
    (learning) =>
      (learning.source_snapshot_id !== null && linkedSnapshotIds.value.has(learning.source_snapshot_id)) ||
      valueLinksRun(learning.evidence_json, props.runId) ||
      valueLinksRun(learning.metadata_json, props.runId),
  ),
)

const linkedExperiments = computed(() =>
  experiments.value.filter(
    (experiment) =>
      (experiment.linked_run_ids_json ?? []).includes(props.runId) ||
      valueLinksRun(experiment.metric_targets_json, props.runId) ||
      valueLinksRun(experiment.metadata_json, props.runId),
  ),
)

const linkedArtifacts = computed(() =>
  artifacts.value.filter(
    (artifact) =>
      valueLinksRun(artifact.metadata_json, props.runId) ||
      valueLinksRun(artifact.provenance_json, props.runId),
  ),
)

async function fetchRunPlans(): Promise<SchemaRunPlanOut[]> {
  const page = await apiFetch<SchemaPageResponseRunPlanSummaryOut>(
    `/api/v1/projects/${props.projectId}/run-plans?run_id=${props.runId}&limit=20`,
  )
  return await Promise.all(
    page.items.map((item) => apiFetch<SchemaRunPlanOut>(`/api/v1/run-plans/${item.id}`)),
  )
}

function durationOf(r: Run): string {
  if (!r.ended_at) return r.status === 'running' ? 'running…' : '—'
  const ms = new Date(r.ended_at).getTime() - new Date(r.started_at).getTime()
  const s = Math.round(ms / 1000)
  return s < 60 ? `${s}s` : `${Math.round(s / 60)}m`
}

function valueLinksRun(value: unknown, runId: number): boolean {
  if (Array.isArray(value)) return value.some((item) => valueLinksRun(item, runId))
  if (typeof value !== 'object' || value === null) return value === runId
  return Object.entries(value).some(([key, child]) => {
    const normalized = key.toLowerCase()
    if (normalized.includes('run')) {
      if (child === runId || child === String(runId)) return true
      if (Array.isArray(child) && child.some((item) => item === runId || item === String(runId))) {
        return true
      }
    }
    return valueLinksRun(child, runId)
  })
}

onMounted(load)
</script>

<template>
  <UiEmptyState
    v-if="loading && !run"
    :title="`Loading run #${runId}`"
    description="Fetching run metadata, run plans, children, and linked project data."
    size="md"
  />
  <UiEmptyState
    v-else-if="!run"
    title="Run not found"
    :description="`Run #${runId} is not available in the local daemon.`"
    size="md"
  />
  <div
    v-else
    class="space-y-5"
  >
    <UiCard section>
      <template #header>
        <h2 class="t-h3 text-fg-strong">
          Summary
        </h2>
        <StatusBadge
          :status="run.status"
          kind="run"
        />
      </template>
      <KvList
        :items="summary"
        :two-column="true"
      />
    </UiCard>

    <section
      class="space-y-3"
      aria-label="Run plans"
    >
      <UiSectionHeader title="Run plans">
        <template #actions>
          <UiBadge>{{ runPlans.length }}</UiBadge>
        </template>
      </UiSectionHeader>
      <p
        v-if="runPlans.length === 0"
        class="text-sm text-fg-muted"
      >
        No run plans for this run.
      </p>
      <RunPlanRenderer
        v-for="plan in runPlans"
        :key="plan.id"
        :plan="plan"
        :action-calls="actionCalls"
      />
    </section>

    <div
      v-if="
        actionCalls.length > 0 ||
          contextSnapshots.length > 0 ||
          observations.length > 0 ||
          decisions.length > 0 ||
          linkedLearnings.length > 0 ||
          linkedExperiments.length > 0
      "
      class="grid items-start gap-5 xl:grid-cols-2"
    >
      <UiCard
        v-if="actionCalls.length > 0"
        section
      >
        <template #header>
          <h2 class="t-h3 text-fg-strong">
            Action calls
          </h2>
          <UiBadge>{{ actionCalls.length }}</UiBadge>
        </template>
        <ul class="space-y-2">
          <li
            v-for="call in actionCalls"
            :key="call.id"
            class="rounded-md border border-subtle bg-bg-surface-alt p-2.5"
          >
            <div class="mb-2 flex flex-wrap items-center gap-2">
              <span class="font-mono text-2xs text-fg-subtle">#{{ call.id }}</span>
              <span class="text-sm font-medium text-fg-default">{{ call.plugin_slug }}.{{ call.action_key }}</span>
              <StatusBadge
                :status="call.status"
                kind="job"
                :small="true"
              />
            </div>
            <UiJsonBlock
              :data="sanitizeForDisplay({ request: call.request_json, response: call.response_json })"
              density="compact"
              max-height="12rem"
              wrap
            />
          </li>
        </ul>
      </UiCard>

      <UiCard
        v-if="contextSnapshots.length > 0"
        section
      >
        <template #header>
          <h2 class="t-h3 text-fg-strong">
            Context snapshots
          </h2>
          <UiBadge>{{ contextSnapshots.length }}</UiBadge>
        </template>
        <ul class="space-y-2">
          <li
            v-for="snapshot in contextSnapshots"
            :key="snapshot.id"
            class="rounded-md border border-subtle bg-bg-surface-alt p-2.5"
          >
            <div class="mb-2 flex items-center justify-between gap-2">
              <span class="min-w-0 truncate text-sm font-medium text-fg-default">
                {{ snapshot.name ?? `Snapshot #${snapshot.id}` }}
              </span>
              <span class="shrink-0 text-2xs text-fg-subtle">{{ formatDateTime(snapshot.created_at) }}</span>
            </div>
            <UiJsonBlock
              :data="sanitizeForDisplay(snapshot.summary_json ?? snapshot.query_json)"
              density="compact"
              max-height="12rem"
              wrap
            />
          </li>
        </ul>
      </UiCard>

      <UiCard
        v-if="observations.length > 0"
        section
      >
        <template #header>
          <h2 class="t-h3 text-fg-strong">
            Observations
          </h2>
          <UiBadge>{{ observations.length }}</UiBadge>
        </template>
        <ul class="space-y-2">
          <li
            v-for="observation in observations"
            :key="observation.id"
            class="rounded-md border border-subtle bg-bg-surface-alt p-2.5"
          >
            <div class="mb-2 flex flex-wrap items-center gap-2">
              <span class="font-mono text-2xs text-fg-subtle">#{{ observation.id }}</span>
              <span class="text-sm font-medium text-fg-default">Experiment #{{ observation.experiment_id }}</span>
              <span class="text-2xs text-fg-subtle">{{ formatDateTime(observation.observed_at) }}</span>
            </div>
            <UiJsonBlock
              :data="sanitizeForDisplay(observation)"
              density="compact"
              max-height="12rem"
              wrap
            />
          </li>
        </ul>
      </UiCard>

      <UiCard
        v-if="decisions.length > 0"
        section
      >
        <template #header>
          <h2 class="t-h3 text-fg-strong">
            Decisions
          </h2>
          <UiBadge>{{ decisions.length }}</UiBadge>
        </template>
        <ul class="space-y-2">
          <li
            v-for="decision in decisions"
            :key="decision.id"
            class="rounded-md border border-subtle bg-bg-surface-alt p-2.5"
          >
            <div class="mb-1 flex items-center justify-between gap-2">
              <span class="min-w-0 truncate text-sm font-medium text-fg-default">
                {{ decision.title ?? `Decision #${decision.id}` }}
              </span>
              <StatusBadge
                :status="decision.status"
                kind="job"
                :small="true"
              />
            </div>
            <p class="text-sm text-fg-default">
              {{ decision.decision }}
            </p>
          </li>
        </ul>
      </UiCard>

      <UiCard
        v-if="linkedLearnings.length > 0"
        section
      >
        <template #header>
          <h2 class="t-h3 text-fg-strong">
            Learnings
          </h2>
          <UiBadge>{{ linkedLearnings.length }}</UiBadge>
        </template>
        <ul class="space-y-2">
          <li
            v-for="learning in linkedLearnings"
            :key="learning.id"
            class="rounded-md border border-subtle bg-bg-surface-alt p-2.5 text-sm text-fg-default"
          >
            {{ learning.statement }}
          </li>
        </ul>
      </UiCard>

      <UiCard
        v-if="linkedExperiments.length > 0"
        section
      >
        <template #header>
          <h2 class="t-h3 text-fg-strong">
            Experiments
          </h2>
          <UiBadge>{{ linkedExperiments.length }}</UiBadge>
        </template>
        <ul class="space-y-2">
          <li
            v-for="experiment in linkedExperiments"
            :key="experiment.id"
            class="rounded-md border border-subtle bg-bg-surface-alt p-2.5 text-sm"
          >
            <div class="mb-1 flex items-center justify-between gap-2">
              <span class="min-w-0 truncate font-medium text-fg-default">
                {{ experiment.name ?? experiment.key ?? `Experiment #${experiment.id}` }}
              </span>
              <StatusBadge
                :status="experiment.status"
                kind="job"
                :small="true"
              />
            </div>
            <p class="text-fg-muted">
              {{ experiment.hypothesis }}
            </p>
          </li>
        </ul>
      </UiCard>
    </div>

    <UiCard section>
      <template #header>
        <h2 class="t-h3 text-fg-strong">
          Artifacts
        </h2>
        <UiBadge>{{ linkedArtifacts.length }}</UiBadge>
      </template>
      <p
        v-if="linkedArtifacts.length === 0"
        class="text-sm text-fg-muted"
      >
        No artifacts for this run.
      </p>
      <div
        v-else
        class="space-y-3"
      >
        <ArtifactRenderer
          v-for="artifact in linkedArtifacts"
          :key="artifact.id"
          :artifact="artifact"
        />
      </div>
    </UiCard>

    <UiAdvancedJsonPanel
      v-if="hasMetadata"
      title="Advanced metadata"
      summary="Raw run context"
      :data="sanitizeForDisplay(run.metadata_json)"
    />

    <UiCard section>
      <template #header>
        <h2 class="t-h3 text-fg-strong">
          Child runs
        </h2>
        <UiBadge>{{ children.length }}</UiBadge>
      </template>
      <p
        v-if="children.length === 0"
        class="text-sm text-fg-muted"
      >
        No child runs for this run.
      </p>
      <ul
        v-else
        class="space-y-1.5 text-sm"
      >
        <li
          v-for="c in children"
          :key="c.id"
          class="flex items-center justify-between gap-3 rounded-md border border-subtle bg-bg-surface-alt px-2.5 py-2"
        >
          <RouterLink
            :to="`/projects/${projectId}/runs/${c.id}`"
            class="focus-ring rounded-sm text-fg-link hover:underline"
          >
            #{{ c.id }} · {{ c.kind }}
          </RouterLink>
          <StatusBadge
            :status="c.status"
            kind="run"
            :small="true"
          />
        </li>
      </ul>
    </UiCard>
  </div>
</template>
