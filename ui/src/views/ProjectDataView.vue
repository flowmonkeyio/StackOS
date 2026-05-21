<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute } from 'vue-router'

import DataTable from '@/components/DataTable.vue'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import ArtifactRenderer from '@/components/renderers/ArtifactRenderer.vue'
import { UiBadge, UiCallout, UiJsonBlock, UiPageShell, UiPanel, UiSectionHeader, UiSegmentedControl } from '@/components/ui'
import type { DataTableColumn } from '@/components/types'
import type {
  SchemaContextSnapshotOut,
  SchemaDecisionOut,
  SchemaExperimentObservationOut,
  SchemaExperimentOut,
  SchemaLearningOut,
  SchemaMetricSnapshotOut,
  SchemaProjectEventOut,
} from '@/api'
import { formatDateTime, sanitizeForDisplay } from '@/lib/stackos/json'
import { useProjectDataStore } from '@/stores/projectData'

type DataTab =
  | 'timeline'
  | 'learnings'
  | 'experiments'
  | 'observations'
  | 'decisions'
  | 'snapshots'
  | 'artifacts'
  | 'metrics'

const route = useRoute()
const projectData = useProjectDataStore()
const {
  timeline,
  snapshots,
  learnings,
  experiments,
  observations,
  decisions,
  metrics,
  artifacts,
  loading,
  error,
} = storeToRefs(projectData)

const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const activeTab = ref<DataTab>('timeline')

const tabOptions = [
  { key: 'timeline', label: 'Timeline' },
  { key: 'learnings', label: 'Learnings' },
  { key: 'experiments', label: 'Experiments' },
  { key: 'observations', label: 'Observations' },
  { key: 'decisions', label: 'Decisions' },
  { key: 'snapshots', label: 'Snapshots' },
  { key: 'artifacts', label: 'Artifacts' },
  { key: 'metrics', label: 'Metrics' },
]

const timelineColumns: DataTableColumn<SchemaProjectEventOut>[] = [
  { key: 'event_type', label: 'Event' },
  { key: 'title', label: 'Title', format: (value) => String(value ?? '-') },
  { key: 'source_type', label: 'Source' },
  { key: 'occurred_at', label: 'Occurred', format: (value) => formatDateTime(String(value)) },
]

const learningColumns: DataTableColumn<SchemaLearningOut>[] = [
  { key: 'statement', label: 'Statement' },
  { key: 'domain', label: 'Domain', format: (value) => String(value ?? '-') },
  { key: 'confidence', label: 'Confidence' },
  { key: 'review_state', label: 'Review' },
]

const experimentColumns: DataTableColumn<SchemaExperimentOut>[] = [
  { key: 'name', label: 'Name', format: (value, row) => String(value ?? (row as SchemaExperimentOut).key ?? '-') },
  { key: 'domain', label: 'Domain', format: (value) => String(value ?? '-') },
  { key: 'status', label: 'Status' },
  { key: 'hypothesis', label: 'Hypothesis' },
]

const decisionColumns: DataTableColumn<SchemaDecisionOut>[] = [
  { key: 'title', label: 'Title', format: (value) => String(value ?? '-') },
  { key: 'decision', label: 'Decision' },
  { key: 'status', label: 'Status' },
  { key: 'created_at', label: 'Created', format: (value) => formatDateTime(String(value)) },
]

const observationColumns: DataTableColumn<SchemaExperimentObservationOut>[] = [
  { key: 'experiment_id', label: 'Experiment', format: (value) => `#${value}` },
  { key: 'variant_key', label: 'Variant', format: (value) => String(value ?? '-') },
  { key: 'summary', label: 'Summary', format: (value) => String(value ?? '-') },
  { key: 'observed_at', label: 'Observed', format: (value) => formatDateTime(String(value)) },
]

const snapshotColumns: DataTableColumn<SchemaContextSnapshotOut>[] = [
  { key: 'name', label: 'Name', format: (value) => String(value ?? '-') },
  { key: 'run_id', label: 'Run', format: (value) => (value ? `#${value}` : '-') },
  { key: 'created_at', label: 'Created', format: (value) => formatDateTime(String(value)) },
]

const metricColumns: DataTableColumn<SchemaMetricSnapshotOut>[] = [
  { key: 'metric_key', label: 'Metric' },
  { key: 'metric_value', label: 'Value', format: (value) => String(value ?? '-') },
  { key: 'source_type', label: 'Source', format: (value) => String(value ?? '-') },
  { key: 'captured_at', label: 'Captured', format: (value) => formatDateTime(String(value)) },
]

function setTab(key: string | number): void {
  activeTab.value = String(key) as DataTab
}

async function load(): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  await projectData.refresh(projectId.value)
}

onMounted(load)
watch(projectId, load)
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Project Data"
      description="Context snapshots, learnings, experiments, decisions, artifacts, metrics, and timeline."
      :breadcrumbs="[{ label: 'Project Data' }]"
    />

    <UiCallout
      v-if="error"
      tone="danger"
    >
      {{ error }}
    </UiCallout>

    <div class="grid gap-3 md:grid-cols-4 xl:grid-cols-8">
      <UiPanel class="p-3">
        <div class="text-xs text-fg-muted">Events</div>
        <div class="text-2xl font-semibold">{{ timeline.length }}</div>
      </UiPanel>
      <UiPanel class="p-3">
        <div class="text-xs text-fg-muted">Learnings</div>
        <div class="text-2xl font-semibold">{{ learnings.length }}</div>
      </UiPanel>
      <UiPanel class="p-3">
        <div class="text-xs text-fg-muted">Experiments</div>
        <div class="text-2xl font-semibold">{{ experiments.length }}</div>
      </UiPanel>
      <UiPanel class="p-3">
        <div class="text-xs text-fg-muted">Observations</div>
        <div class="text-2xl font-semibold">{{ observations.length }}</div>
      </UiPanel>
      <UiPanel class="p-3">
        <div class="text-xs text-fg-muted">Decisions</div>
        <div class="text-2xl font-semibold">{{ decisions.length }}</div>
      </UiPanel>
      <UiPanel class="p-3">
        <div class="text-xs text-fg-muted">Snapshots</div>
        <div class="text-2xl font-semibold">{{ snapshots.length }}</div>
      </UiPanel>
      <UiPanel class="p-3">
        <div class="text-xs text-fg-muted">Artifacts</div>
        <div class="text-2xl font-semibold">{{ artifacts.length }}</div>
      </UiPanel>
      <UiPanel class="p-3">
        <div class="text-xs text-fg-muted">Metrics</div>
        <div class="text-2xl font-semibold">{{ metrics.length }}</div>
      </UiPanel>
    </div>

    <UiPanel class="p-4">
      <UiSegmentedControl
        :model-value="activeTab"
        :options="tabOptions"
        label="Project data"
        @select="setTab"
      />
    </UiPanel>

    <UiPanel
      v-if="activeTab === 'timeline'"
      class="p-4"
    >
      <UiSectionHeader title="Timeline" as="h3" />
      <DataTable
        :items="timeline"
        :columns="timelineColumns"
        :loading="loading"
        aria-label="Project timeline"
        empty-message="No timeline events."
      />
    </UiPanel>

    <UiPanel
      v-else-if="activeTab === 'learnings'"
      class="p-4"
    >
      <UiSectionHeader title="Learnings" as="h3" />
      <DataTable
        :items="learnings"
        :columns="learningColumns"
        :loading="loading"
        aria-label="Learnings"
        empty-message="No learnings."
      >
        <template #cell:review_state="{ value }">
          <UiBadge>{{ value }}</UiBadge>
        </template>
      </DataTable>
    </UiPanel>

    <UiPanel
      v-else-if="activeTab === 'experiments'"
      class="p-4"
    >
      <UiSectionHeader title="Experiments" as="h3" />
      <DataTable
        :items="experiments"
        :columns="experimentColumns"
        :loading="loading"
        aria-label="Experiments"
        empty-message="No experiments."
      >
        <template #cell:status="{ value }">
          <UiBadge tone="info">{{ value }}</UiBadge>
        </template>
      </DataTable>
    </UiPanel>

    <UiPanel
      v-else-if="activeTab === 'observations'"
      class="p-4"
    >
      <UiSectionHeader title="Observations" as="h3" />
      <DataTable
        :items="observations"
        :columns="observationColumns"
        :loading="loading"
        aria-label="Observations"
        empty-message="No observations."
      />
    </UiPanel>

    <UiPanel
      v-else-if="activeTab === 'decisions'"
      class="p-4"
    >
      <UiSectionHeader title="Decisions" as="h3" />
      <DataTable
        :items="decisions"
        :columns="decisionColumns"
        :loading="loading"
        aria-label="Decisions"
        empty-message="No decisions."
      />
    </UiPanel>

    <UiPanel
      v-else-if="activeTab === 'snapshots'"
      class="p-4"
    >
      <UiSectionHeader title="Context Snapshots" as="h3" />
      <DataTable
        :items="snapshots"
        :columns="snapshotColumns"
        :loading="loading"
        aria-label="Context snapshots"
        empty-message="No snapshots."
      />
      <details
        v-for="snapshot in snapshots.slice(0, 3)"
        :key="snapshot.id"
        class="mt-3 rounded-md border border-subtle bg-bg-surface"
      >
        <summary class="cursor-pointer px-3 py-2 text-sm font-medium focus-ring">
          {{ snapshot.name ?? `Snapshot #${snapshot.id}` }}
        </summary>
        <div class="border-t border-subtle p-3">
          <UiJsonBlock
            :data="sanitizeForDisplay(snapshot)"
            density="compact"
            max-height="14rem"
            wrap
          />
        </div>
      </details>
    </UiPanel>

    <section
      v-else-if="activeTab === 'artifacts'"
      class="space-y-3"
    >
      <UiSectionHeader title="Artifacts" />
      <p
        v-if="artifacts.length === 0"
        class="rounded-md border border-dashed border-subtle bg-bg-surface-alt px-4 py-5 text-sm text-fg-muted"
      >
        No artifacts.
      </p>
      <ArtifactRenderer
        v-for="artifact in artifacts"
        :key="artifact.id"
        :artifact="artifact"
      />
    </section>

    <UiPanel
      v-else
      class="p-4"
    >
      <UiSectionHeader title="Metrics" as="h3" />
      <DataTable
        :items="metrics"
        :columns="metricColumns"
        :loading="loading"
        aria-label="Metrics"
        empty-message="No metric snapshots."
      />
    </UiPanel>
  </UiPageShell>
</template>
