<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'

import DataTable from '@/components/DataTable.vue'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import ArtifactRenderer from '@/components/renderers/ArtifactRenderer.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import TabBar from '@/components/TabBar.vue'
import {
  UiAdvancedJsonPanel,
  UiButton,
  UiCallout,
  UiCountBadge,
  UiEmptyState,
  UiPageShell,
  UiSectionHeader,
} from '@/components/ui'
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
import { newestFirst } from '@/lib/stackos/time'
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
const router = useRouter()
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

// The API returns rows ascending; operator tables lead with the latest.
const timelineNewest = computed(() => newestFirst(timeline.value, (row) => row.occurred_at))
const learningsNewest = computed(() => newestFirst(learnings.value, (row) => row.created_at))
const experimentsNewest = computed(() => newestFirst(experiments.value, (row) => row.created_at))
const observationsNewest = computed(() =>
  newestFirst(observations.value, (row) => row.observed_at ?? row.created_at),
)
const decisionsNewest = computed(() => newestFirst(decisions.value, (row) => row.created_at))
const snapshotsNewest = computed(() => newestFirst(snapshots.value, (row) => row.created_at))
const artifactsNewest = computed(() => newestFirst(artifacts.value, (row) => row.created_at))
const metricsNewest = computed(() =>
  newestFirst(metrics.value, (row) => row.captured_at ?? row.created_at),
)

const TAB_DEFS = [
  { key: 'timeline', label: 'Timeline' },
  { key: 'learnings', label: 'Learnings' },
  { key: 'experiments', label: 'Experiments' },
  { key: 'observations', label: 'Observations' },
  { key: 'decisions', label: 'Decisions' },
  { key: 'snapshots', label: 'Snapshots' },
  { key: 'artifacts', label: 'Artifacts' },
  { key: 'metrics', label: 'Metrics' },
] satisfies Array<{ key: DataTab; label: string }>

const tabKeys = new Set<DataTab>(TAB_DEFS.map((option) => option.key))
const activeTab = ref<DataTab>(tabFromQuery(route.query.tab))

const tabCounts = computed<Record<DataTab, number>>(() => ({
  timeline: timeline.value.length,
  learnings: learnings.value.length,
  experiments: experiments.value.length,
  observations: observations.value.length,
  decisions: decisions.value.length,
  snapshots: snapshots.value.length,
  artifacts: artifacts.value.length,
  metrics: metrics.value.length,
}))

const tabs = computed(() =>
  TAB_DEFS.map((tab) => ({ key: tab.key, label: tab.label, count: tabCounts.value[tab.key] })),
)

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
  const nextTab = normalizeTab(key)
  activeTab.value = nextTab
  syncTabToUrl(nextTab)
}

async function load(): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  await projectData.refresh(projectId.value)
}

onMounted(load)
onBeforeRouteUpdate((to) => {
  activeTab.value = tabFromQuery(to.query.tab)
})

function normalizeTab(value: unknown): DataTab {
  const candidate = String(value ?? '')
  return tabKeys.has(candidate as DataTab) ? (candidate as DataTab) : 'timeline'
}

function tabFromQuery(raw: unknown): DataTab {
  const value = Array.isArray(raw) ? raw[0] : raw
  return normalizeTab(value)
}

function syncTabToUrl(tab: DataTab): void {
  const nextQuery = { ...route.query }
  if (tab === 'timeline') {
    delete nextQuery.tab
  } else {
    nextQuery.tab = tab
  }
  void router.replace({ query: nextQuery })
}
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Project data"
      description="Context snapshots, learnings, experiments, decisions, artifacts, metrics, and timeline."
      :breadcrumbs="[{ label: 'Project data' }]"
    >
      <template #actions>
        <UiButton
          variant="secondary"
          size="sm"
          icon-left="refresh"
          :loading="loading"
          @click="load"
        >
          Refresh
        </UiButton>
      </template>
      <template #tabs>
        <TabBar
          :tabs="tabs"
          :active-key="activeTab"
          aria-label="Project data"
          @change="setTab"
        />
      </template>
    </ProjectPageHeader>

    <UiCallout
      v-if="error"
      tone="danger"
    >
      {{ error }}
    </UiCallout>

    <section
      v-if="activeTab === 'timeline'"
      :id="`cs-tabpanel-timeline`"
      role="tabpanel"
      aria-labelledby="cs-tab-timeline"
      aria-label="Project timeline"
    >
      <DataTable
        :items="timelineNewest"
        :columns="timelineColumns"
        :loading="loading"
        aria-label="Project timeline"
        empty-message="No timeline events yet — events are recorded as agents and plugins act on the project."
      />
    </section>

    <section
      v-else-if="activeTab === 'learnings'"
      :id="`cs-tabpanel-learnings`"
      role="tabpanel"
      aria-labelledby="cs-tab-learnings"
      aria-label="Learnings"
    >
      <DataTable
        :items="learningsNewest"
        :columns="learningColumns"
        :loading="loading"
        aria-label="Learnings"
        empty-message="No learnings yet — agents record durable learnings as they work."
      >
        <template #cell:review_state="{ value }">
          <StatusBadge
            domain="memory"
            :status="String(value)"
            small
          />
        </template>
      </DataTable>
    </section>

    <section
      v-else-if="activeTab === 'experiments'"
      :id="`cs-tabpanel-experiments`"
      role="tabpanel"
      aria-labelledby="cs-tab-experiments"
      aria-label="Experiments"
    >
      <DataTable
        :items="experimentsNewest"
        :columns="experimentColumns"
        :loading="loading"
        aria-label="Experiments"
        empty-message="No experiments yet — agents register experiments to track hypotheses."
      >
        <template #cell:status="{ value }">
          <StatusBadge
            domain="memory"
            :status="String(value)"
            small
          />
        </template>
      </DataTable>
    </section>

    <section
      v-else-if="activeTab === 'observations'"
      :id="`cs-tabpanel-observations`"
      role="tabpanel"
      aria-labelledby="cs-tab-observations"
      aria-label="Observations"
    >
      <DataTable
        :items="observationsNewest"
        :columns="observationColumns"
        :loading="loading"
        aria-label="Observations"
        empty-message="No observations yet — observations are recorded against running experiments."
      />
    </section>

    <section
      v-else-if="activeTab === 'decisions'"
      :id="`cs-tabpanel-decisions`"
      role="tabpanel"
      aria-labelledby="cs-tab-decisions"
      aria-label="Decisions"
    >
      <DataTable
        :items="decisionsNewest"
        :columns="decisionColumns"
        :loading="loading"
        aria-label="Decisions"
        empty-message="No decisions yet — agents log decisions with their rationale."
      >
        <template #cell:status="{ value }">
          <StatusBadge
            domain="memory"
            :status="String(value)"
            small
          />
        </template>
      </DataTable>
    </section>

    <section
      v-else-if="activeTab === 'snapshots'"
      :id="`cs-tabpanel-snapshots`"
      role="tabpanel"
      aria-labelledby="cs-tab-snapshots"
      class="space-y-4"
      aria-label="Context snapshots"
    >
      <DataTable
        :items="snapshotsNewest"
        :columns="snapshotColumns"
        :loading="loading"
        aria-label="Context snapshots"
        empty-message="No snapshots yet — agents store context snapshots during runs."
      />
      <div
        v-if="snapshots.length"
        class="space-y-2"
      >
        <UiSectionHeader
          title="Recent snapshot payloads"
          as="h3"
        >
          <template #actions>
            <UiCountBadge :value="Math.min(snapshots.length, 3)" />
          </template>
        </UiSectionHeader>
        <UiAdvancedJsonPanel
          v-for="snapshot in snapshotsNewest.slice(0, 3)"
          :key="snapshot.id"
          :title="snapshot.name ?? `Snapshot #${snapshot.id}`"
          summary="Raw snapshot"
          :data="sanitizeForDisplay(snapshot)"
          max-height="14rem"
        />
      </div>
    </section>

    <section
      v-else-if="activeTab === 'artifacts'"
      :id="`cs-tabpanel-artifacts`"
      role="tabpanel"
      aria-labelledby="cs-tab-artifacts"
      class="space-y-3"
      aria-label="Artifacts"
    >
      <UiEmptyState
        v-if="artifacts.length === 0"
        title="No artifacts yet"
        description="Runs attach generated files, exports, and reports here as agents work."
        icon="archive"
        size="sm"
        framed
      />
      <ArtifactRenderer
        v-for="artifact in artifactsNewest"
        :key="artifact.id"
        :artifact="artifact"
      />
    </section>

    <section
      v-else
      :id="`cs-tabpanel-metrics`"
      role="tabpanel"
      aria-labelledby="cs-tab-metrics"
      aria-label="Metrics"
    >
      <DataTable
        :items="metricsNewest"
        :columns="metricColumns"
        :loading="loading"
        aria-label="Metrics"
        empty-message="No metric snapshots yet — metrics are captured by runs and triggers."
      />
    </section>
  </UiPageShell>
</template>
