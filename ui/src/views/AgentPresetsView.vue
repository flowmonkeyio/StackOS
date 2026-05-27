<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import DataTable from '@/components/DataTable.vue'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import {
  UiBadge,
  UiCallout,
  UiJsonBlock,
  UiPageShell,
  UiPanel,
  UiSectionHeader,
  UiSegmentedControl,
} from '@/components/ui'
import type { DataTableColumn } from '@/components/types'
import { formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'
import { sanitizeForDisplay } from '@/lib/stackos/json'

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
const loading = ref(false)
const detailLoading = ref(false)
const error = ref<string | null>(null)
const domainFilter = ref<DomainFilter>('sdlc')

const selectedKey = computed(() => String(route.query.preset ?? ''))
const filteredRows = computed(() =>
  domainFilter.value === 'all'
    ? rows.value
    : rows.value.filter((row) => (row.domain ?? 'unknown') === domainFilter.value),
)
const domainOptions = computed(() => [
  { key: 'all', label: 'All' },
  ...Array.from(new Set(rows.value.map((row) => row.domain ?? 'unknown')))
    .sort()
    .map((domain) => ({ key: domain, label: domain === 'sdlc' ? 'SDLC' : domain })),
])
const sdlcCount = computed(() => rows.value.filter((row) => row.domain === 'sdlc').length)
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

async function loadList(): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  loading.value = true
  error.value = null
  try {
    const payload = await callOperation<AgentPresetListOut>('agentPreset.list', {
      project_id: projectId.value,
    })
    rows.value = payload.presets.map((preset) => ({ ...preset, id: preset.key }))
    const key =
      selectedKey.value || rows.value.find((row) => row.domain === 'sdlc')?.key || rows.value[0]?.key
    if (key) await loadDetail(key)
    else selected.value = null
  } catch (err) {
    error.value = formatApiError(err, 'failed to load agent presets')
  } finally {
    loading.value = false
  }
}

async function loadDetail(key: string): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value) || !key) {
    selected.value = null
    return
  }
  detailLoading.value = true
  error.value = null
  try {
    selected.value = await callOperation<AgentPresetDescribeOut>('agentPreset.describe', {
      project_id: projectId.value,
      key,
    })
  } catch (err) {
    error.value = formatApiError(err, 'failed to load agent preset')
  } finally {
    detailLoading.value = false
  }
}

async function selectPreset(row: PresetRow): Promise<void> {
  await router.replace({
    query: {
      ...route.query,
      preset: row.key,
    },
  })
}

function domainLabel(domain?: string | null): string {
  if (!domain) return 'unknown'
  return domain === 'sdlc' ? 'SDLC' : domain
}

function domainTone(
  domain?: string | null,
): 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'accent' {
  if (domain === 'sdlc') return 'warning'
  if (domain === 'core') return 'accent'
  return 'info'
}

onMounted(loadList)
watch(projectId, loadList)
watch(selectedKey, (key) => {
  if (key) void loadDetail(key)
})
watch(domainFilter, () => {
  if (filteredRows.value.length === 0) return
  if (!filteredRows.value.some((row) => row.key === selectedKey.value)) {
    void selectPreset(filteredRows.value[0])
  }
})
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Agent Presets"
      description="Generic agent role contracts that must be adapted to the current project before use."
      :breadcrumbs="[{ label: 'Agent Presets' }]"
    />

    <UiCallout
      v-if="error"
      tone="danger"
    >
      {{ error }}
    </UiCallout>

    <div class="grid gap-3 md:grid-cols-4">
      <UiPanel class="p-4">
        <p class="text-xs font-semibold uppercase text-fg-muted">Presets</p>
        <p class="mt-2 text-2xl font-semibold text-fg-strong">{{ rows.length }}</p>
      </UiPanel>
      <UiPanel class="p-4">
        <p class="text-xs font-semibold uppercase text-fg-muted">SDLC</p>
        <p class="mt-2 text-2xl font-semibold text-fg-strong">{{ sdlcCount }}</p>
      </UiPanel>
      <UiPanel class="p-4">
        <p class="text-xs font-semibold uppercase text-fg-muted">Workflow Linked</p>
        <p class="mt-2 text-2xl font-semibold text-fg-strong">{{ workflowLinkedCount }}</p>
      </UiPanel>
      <UiPanel class="p-4">
        <p class="text-xs font-semibold uppercase text-fg-muted">Require Adaptation</p>
        <p class="mt-2 text-2xl font-semibold text-fg-strong">{{ adaptationRequiredCount }}</p>
      </UiPanel>
    </div>

    <div class="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(30rem,42rem)] xl:items-start">
      <UiPanel class="p-4">
        <UiSectionHeader
          title="Preset Catalog"
          as="h3"
        >
          <template #actions>
            <UiBadge>{{ filteredRows.length }}</UiBadge>
          </template>
        </UiSectionHeader>

        <div class="mb-3 overflow-x-auto pb-1">
          <UiSegmentedControl
            v-model="domainFilter"
            :options="domainOptions"
            label="Domain"
          />
        </div>

        <DataTable
          :items="filteredRows"
          :columns="columns"
          :loading="loading"
          :selected-id="selected?.preset.summary.key"
          max-height="calc(100vh - 22rem)"
          aria-label="Agent presets"
          empty-message="No agent presets."
          interactive
          @row-click="selectPreset"
        >
          <template #cell:domain="{ value }">
            <UiBadge :tone="domainTone(String(value))">
              {{ domainLabel(String(value)) }}
            </UiBadge>
          </template>
          <template #cell:name="{ row }">
            <span class="font-medium text-fg-strong">{{ row.name }}</span>
            <span class="mt-1 block font-mono text-xs text-fg-muted">{{ row.role }}</span>
          </template>
          <template #cell:applies_to_workflows="{ row }">
            <UiBadge :tone="row.applies_to_workflows.length ? 'success' : 'neutral'">
              {{ row.applies_to_workflows.length || 'general' }}
            </UiBadge>
          </template>
        </DataTable>
      </UiPanel>

      <UiPanel class="p-4 xl:sticky xl:top-4 xl:max-h-[calc(100vh-2rem)] xl:overflow-y-auto">
        <UiSectionHeader
          :title="selected?.preset.summary.name ?? 'Preset'"
          as="h3"
        >
          <template
            v-if="selected"
            #actions
          >
            <UiBadge :tone="domainTone(selected.preset.summary.domain)">
              {{ domainLabel(selected.preset.summary.domain) }}
            </UiBadge>
            <UiBadge tone="warning">adapt</UiBadge>
          </template>
        </UiSectionHeader>

        <div
          v-if="detailLoading"
          class="py-8 text-center text-sm text-fg-muted"
        >
          Loading...
        </div>
        <div
          v-else-if="!selected"
          class="py-8 text-sm text-fg-muted"
        >
          No preset selected.
        </div>
        <div
          v-else
          class="space-y-4"
        >
          <p class="text-sm text-fg-muted">
            {{ selected.preset.summary.description }}
          </p>

          <dl class="grid gap-3 text-sm md:grid-cols-2">
            <div class="min-w-0">
              <dt class="text-xs text-fg-muted">Key</dt>
              <dd class="truncate font-mono">{{ selected.preset.summary.key }}</dd>
            </div>
            <div class="min-w-0">
              <dt class="text-xs text-fg-muted">Role</dt>
              <dd class="truncate font-mono">{{ selected.preset.summary.role }}</dd>
            </div>
            <div class="min-w-0">
              <dt class="text-xs text-fg-muted">Type</dt>
              <dd class="truncate">{{ selected.preset.summary.agent_type }}</dd>
            </div>
            <div class="min-w-0">
              <dt class="text-xs text-fg-muted">Source</dt>
              <dd class="truncate">
                {{ selected.preset.summary.plugin_slug ?? selected.preset.summary.source }}
              </dd>
            </div>
          </dl>

          <section class="rounded-md border border-subtle bg-bg-surface p-3">
            <h4 class="text-sm font-semibold text-fg-strong">Mission</h4>
            <p class="mt-2 text-sm text-fg-muted">
              {{ selected.preset.preset.prompt_contract.mission }}
            </p>
          </section>

          <section class="rounded-md border border-subtle bg-bg-surface p-3">
            <h4 class="text-sm font-semibold text-fg-strong">Project Adaptation</h4>
            <p class="mt-2 text-sm text-fg-muted">
              {{ selected.project_adaptation.instruction }}
            </p>
            <div class="mt-3 flex flex-wrap gap-2">
              <UiBadge tone="warning">do not use verbatim</UiBadge>
              <UiBadge tone="info">project-specific setup required</UiBadge>
            </div>
          </section>

          <div class="grid gap-4 lg:grid-cols-2">
            <section class="rounded-md border border-subtle bg-bg-surface p-3">
              <h4 class="text-sm font-semibold text-fg-strong">Must Do</h4>
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
              <h4 class="text-sm font-semibold text-fg-strong">Success Criteria</h4>
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
            <h4 class="text-sm font-semibold text-fg-strong">Workflow Coverage</h4>
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
      </UiPanel>
    </div>
  </UiPageShell>
</template>
