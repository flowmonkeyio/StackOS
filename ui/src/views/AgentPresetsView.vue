<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'

import DataTable from '@/components/DataTable.vue'
import InspectableDetailDrawer from '@/components/InspectableDetailDrawer.vue'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import {
  UiBadge,
  UiButton,
  UiCallout,
  UiCountBadge,
  UiDescriptionList,
  UiJsonBlock,
  UiMetricCard,
  UiPageShell,
  UiSegmentedControl,
  UiSkeleton,
  UiToolbar,
} from '@/components/ui'
import type { DataTableColumn } from '@/components/types'
import { formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import { sanitizeForDisplay } from '@/lib/stackos/json'
import { stackOsPluginDisplayOrder, stackOsPluginLabel } from '@/lib/stackos/nav'

interface AgentPromptContract {
  mission: string
  responsibilities?: string[]
  must_do?: string[]
  must_not_do?: string[]
  handoff_inputs?: string[]
  handoff_outputs?: string[]
  success_criteria?: string[]
  self_check?: string[]
}

interface AgentProjectAdaptation {
  required: boolean
  do_not_use_verbatim: boolean
  instruction: string
  prompt_assembly_order?: string[]
  required_agent_action?: string
}

interface AgentPresetSpec {
  key: string
  name: string
  version: string
  description: string
  domain?: string | null
  role: string
  agent_type: string
  prompt_contract: AgentPromptContract
  project_adaptation: AgentProjectAdaptation
  recommended_tools?: string[]
  workflow_roles?: string[]
  applies_to_workflows?: string[]
  generic_preset: boolean
}

interface AgentPresetSummary {
  key: string
  name: string
  version: string
  description: string
  domain?: string | null
  role: string
  agent_type: string
  source: string
  precedence: number
  plugin_slug?: string | null
  origin_path?: string | null
  workflow_roles: string[]
  applies_to_workflows: string[]
  generic_preset: boolean
  adaptation_required: boolean
  shadowed_by?: string | null
}

interface LoadedAgentPreset {
  summary: AgentPresetSummary
  preset: AgentPresetSpec
}

interface AgentPresetListOut {
  presets: AgentPresetSummary[]
  include_shadowed?: boolean
}

interface AgentPresetDescribeOut {
  preset: LoadedAgentPreset
  project_adaptation: {
    adaptation_required: boolean
    do_not_use_verbatim: boolean
    instruction: string
    prompt_assembly_order: string[]
    required_agent_action: string
  }
  setup_guidance: string[]
}

type DomainFilter = 'all' | string
type PresetRow = AgentPresetSummary & { id: string }

const route = useRoute()
const router = useRouter()

const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const rows = ref<PresetRow[]>([])
const selected = ref<AgentPresetDescribeOut | null>(null)
const detailOpen = ref(false)
const loading = ref(false)
const detailLoading = ref(false)
const error = ref<string | null>(null)
const domainFilter = ref<DomainFilter>('engineering')

const filteredRows = computed(() =>
  domainFilter.value === 'all'
    ? rows.value
    : rows.value.filter((row) => (row.domain ?? 'unknown') === domainFilter.value),
)
const domainOptions = computed(() => [
  { key: 'all', label: 'All' },
  ...Array.from(new Set(rows.value.map((row) => row.domain ?? 'unknown')))
    .sort(
      (a, b) =>
        stackOsPluginDisplayOrder(a) - stackOsPluginDisplayOrder(b) ||
        stackOsPluginLabel(a).localeCompare(stackOsPluginLabel(b)),
    )
    .map((domain) => ({
      key: domain,
      label: stackOsPluginLabel(domain),
    })),
])
const engineeringCount = computed(
  () => rows.value.filter((row) => row.domain === 'engineering').length,
)
const workflowLinkedCount = computed(
  () => rows.value.filter((row) => row.applies_to_workflows.length > 0).length,
)
const adaptationRequiredCount = computed(
  () => rows.value.filter((row) => row.adaptation_required).length,
)
const selectedRaw = computed(() => sanitizeForDisplay(selected.value?.preset.preset ?? null))

const columns: DataTableColumn<PresetRow>[] = [
  { key: 'domain', label: 'Domain', widthClass: 'w-24' },
  { key: 'name', label: 'Preset' },
  { key: 'applies_to_workflows', label: 'Workflows', widthClass: 'w-28' },
]

function parseProjectId(raw: unknown): number {
  return Number.parseInt(String(Array.isArray(raw) ? raw[0] : raw), 10)
}

async function loadListFor(nextProjectId = projectId.value): Promise<void> {
  if (!nextProjectId || Number.isNaN(nextProjectId)) return
  loading.value = true
  error.value = null
  try {
    const payload = await callOperation<AgentPresetListOut>('agentPreset.list', {
      project_id: nextProjectId,
    })
    rows.value = payload.presets.map((preset) => ({ ...preset, id: preset.key }))
    selected.value = null
    detailOpen.value = false
  } catch (err) {
    error.value = formatApiError(err, 'failed to load agent presets')
  } finally {
    loading.value = false
  }
}

async function loadDetailFor(key: string, nextProjectId = projectId.value): Promise<void> {
  if (!nextProjectId || Number.isNaN(nextProjectId) || !key) {
    selected.value = null
    return
  }
  detailLoading.value = true
  error.value = null
  try {
    selected.value = await callOperation<AgentPresetDescribeOut>('agentPreset.describe', {
      project_id: nextProjectId,
      key,
    })
  } catch (err) {
    error.value = formatApiError(err, 'failed to load agent preset')
  } finally {
    detailLoading.value = false
  }
}

async function selectPreset(row: PresetRow): Promise<void> {
  detailOpen.value = true
  await loadDetailFor(row.key)
  await router.replace({
    query: {
      ...route.query,
      preset: row.key,
    },
  })
}

function setDomain(value: string | number): void {
  domainFilter.value = String(value)
  const currentSelectedKey = selected.value?.preset.summary.key ?? ''
  if (
    (currentSelectedKey || detailOpen.value) &&
    !filteredRows.value.some((row) => row.key === currentSelectedKey)
  ) {
    selected.value = null
    detailOpen.value = false
    const query = { ...route.query }
    delete query.preset
    void router.replace({ query })
  }
}

function domainLabel(domain?: string | null): string {
  return stackOsPluginLabel(domain)
}

onMounted(() => loadListFor())
onBeforeRouteUpdate((to) => {
  const nextProjectId = parseProjectId(to.params.id)
  if (nextProjectId !== projectId.value) {
    void loadListFor(nextProjectId)
  }
})
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Agent presets"
      description="Generic agent role contracts that must be adapted to the current project before use."
      :breadcrumbs="[{ label: 'Agent presets' }]"
    >
      <template #actions>
        <UiButton
          variant="secondary"
          size="sm"
          icon-left="refresh"
          :loading="loading"
          @click="loadListFor()"
        >
          Refresh
        </UiButton>
      </template>
    </ProjectPageHeader>

    <UiCallout
      v-if="error"
      tone="danger"
    >
      {{ error }}
    </UiCallout>

    <div class="grid gap-3 md:grid-cols-4">
      <UiMetricCard
        label="Presets"
        :value="rows.length"
        density="compact"
      />
      <UiMetricCard
        label="Engineering"
        :value="engineeringCount"
        density="compact"
      />
      <UiMetricCard
        label="Workflow linked"
        :value="workflowLinkedCount"
        density="compact"
      />
      <UiMetricCard
        label="Require adaptation"
        :value="adaptationRequiredCount"
        density="compact"
      />
    </div>

    <section
      aria-label="Agent preset catalog"
      class="space-y-4"
    >
      <UiToolbar
        variant="sunken"
        aria-label="Agent preset filters"
        density="comfortable"
      >
        <UiSegmentedControl
          :model-value="domainFilter"
          :options="domainOptions"
          label="Domain"
          @select="setDomain"
        />
        <template #right>
          <UiCountBadge :value="filteredRows.length" />
        </template>
      </UiToolbar>

      <DataTable
        :items="filteredRows"
        :columns="columns"
        :loading="loading"
        :selected-id="detailOpen ? selected?.preset.summary.key : null"
        aria-label="Agent presets"
        empty-message="No agent presets — presets ship with StackOS core and enabled plugins."
        interactive
        @row-click="selectPreset"
      >
        <template #cell:domain="{ value }">
          <UiBadge variant="outline">
            {{ domainLabel(String(value)) }}
          </UiBadge>
        </template>
        <template #cell:name="{ row }">
          <span class="font-medium text-fg-strong">{{ row.name }}</span>
          <span class="mt-1 block font-mono text-xs text-fg-muted">{{ row.role }}</span>
        </template>
        <template #cell:applies_to_workflows="{ row }">
          <UiCountBadge
            v-if="row.applies_to_workflows.length"
            :value="row.applies_to_workflows.length"
          />
          <span
            v-else
            class="text-xs text-fg-muted"
          >
            general
          </span>
        </template>
      </DataTable>
    </section>

    <InspectableDetailDrawer
      v-model="detailOpen"
      :title="selected?.preset.summary.name ?? 'Preset'"
      :description="selected?.preset.summary.description"
      size="xl"
      :has-detail="Boolean(selected) || detailLoading"
      empty-title="No preset selected"
      empty-description="Select an agent preset row to inspect its role contract and adaptation guidance."
    >
      <template #header="{ titleId, descriptionId }">
        <div class="min-w-0">
          <div class="flex flex-wrap items-center gap-2">
            <h2
              :id="titleId"
              class="t-h2 text-fg-strong"
            >
              {{ selected?.preset.summary.name ?? 'Preset' }}
            </h2>
            <UiBadge
              v-if="selected"
              variant="outline"
            >
              {{ domainLabel(selected.preset.summary.domain) }}
            </UiBadge>
            <UiBadge
              v-if="selected"
              tone="warning"
            >
              adapt
            </UiBadge>
          </div>
          <p
            v-if="selected"
            :id="descriptionId"
            class="mt-1 text-sm text-fg-muted"
          >
            {{ selected.preset.summary.description }}
          </p>
        </div>
      </template>

      <div
        v-if="detailLoading"
        class="space-y-3"
      >
        <UiSkeleton class="h-4 w-3/4" />
        <UiSkeleton class="h-24 w-full" />
        <UiSkeleton class="h-24 w-full" />
      </div>
      <div
        v-else-if="selected"
        class="space-y-4"
      >
        <p class="text-sm text-fg-muted">
          {{ selected.preset.summary.description }}
        </p>

        <UiDescriptionList
          layout="grid"
          :columns="2"
          density="compact"
          :items="[
            { label: 'Key', value: selected.preset.summary.key, mono: true },
            { label: 'Role', value: selected.preset.summary.role, mono: true },
            { label: 'Type', value: selected.preset.summary.agent_type },
            {
              label: 'Source',
              value: selected.preset.summary.plugin_slug ?? selected.preset.summary.source,
            },
          ]"
          aria-label="Preset identity"
        />

        <section class="rounded-md border border-subtle bg-bg-surface p-3">
          <h4 class="text-sm font-semibold text-fg-strong">
            Mission
          </h4>
          <p class="mt-2 text-sm text-fg-muted">
            {{ selected.preset.preset.prompt_contract.mission }}
          </p>
        </section>

        <section class="rounded-md border border-subtle bg-bg-surface p-3">
          <h4 class="text-sm font-semibold text-fg-strong">
            Project adaptation
          </h4>
          <p class="mt-2 text-sm text-fg-muted">
            {{ selected.project_adaptation.instruction }}
          </p>
          <div class="mt-3 flex flex-wrap gap-2">
            <UiBadge tone="warning">
              do not use verbatim
            </UiBadge>
            <UiBadge tone="info">
              project-specific setup required
            </UiBadge>
          </div>
        </section>

        <div class="grid gap-4 lg:grid-cols-2">
          <section class="rounded-md border border-subtle bg-bg-surface p-3">
            <h4 class="text-sm font-semibold text-fg-strong">
              Must do
            </h4>
            <ul class="mt-2 space-y-1 text-sm text-fg-muted">
              <li
                v-for="item in selected.preset.preset.prompt_contract.must_do ?? []"
                :key="item"
              >
                {{ item }}
              </li>
            </ul>
          </section>
          <section class="rounded-md border border-subtle bg-bg-surface p-3">
            <h4 class="text-sm font-semibold text-fg-strong">
              Success criteria
            </h4>
            <ul class="mt-2 space-y-1 text-sm text-fg-muted">
              <li
                v-for="item in selected.preset.preset.prompt_contract.success_criteria ?? []"
                :key="item"
              >
                {{ item }}
              </li>
            </ul>
          </section>
        </div>

        <section
          v-if="selected.preset.summary.applies_to_workflows.length"
          class="rounded-md border border-subtle bg-bg-surface p-3"
        >
          <h4 class="text-sm font-semibold text-fg-strong">
            Workflow coverage
          </h4>
          <div class="mt-2 flex flex-wrap gap-2">
            <UiBadge
              v-for="workflow in selected.preset.summary.applies_to_workflows"
              :key="workflow"
              tone="accent"
            >
              {{ workflow }}
            </UiBadge>
          </div>
        </section>

        <details class="rounded-md border border-subtle bg-bg-surface">
          <summary class="cursor-pointer px-3 py-2 text-sm font-medium text-fg-default focus-ring">
            Preset JSON
          </summary>
          <div class="border-t border-subtle p-3">
            <UiJsonBlock
              :data="selectedRaw"
              density="compact"
              max-height="18rem"
              wrap
            />
          </div>
        </details>
      </div>
    </InspectableDetailDrawer>
  </UiPageShell>
</template>
