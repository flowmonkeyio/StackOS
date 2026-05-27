<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  UiBadge,
  UiButton,
  UiCallout,
  UiMetricCard,
  UiPanel,
  UiProgressBar,
  UiSectionHeader,
} from '@/components/ui'
import type {
  SchemaActionOut,
  SchemaAuthProviderOut,
  SchemaAuthStatusOut,
  SchemaHealthResponse,
  SchemaLoadedWorkflowTemplate,
  SchemaPageResponseRunOut,
  SchemaPluginOut,
  SchemaWorkflowTemplateListOut,
  SchemaWorkflowTemplateSummaryOut,
} from '@/api'
import { apiFetch, formatApiError } from '@/lib/client'
import { callOperation } from '@/lib/operations'

type ChecklistStatus = 'done' | 'todo' | 'attention'

interface AgentPresetSummary {
  key: string
  name: string
  domain?: string | null
  role?: string | null
  plugin_slug?: string | null
  adaptation_required?: boolean
  applies_to_workflows?: string[]
}

interface AgentPresetListOut {
  presets: AgentPresetSummary[]
  include_shadowed?: boolean
}

interface ChecklistItem {
  key: string
  label: string
  detail: string
  status: ChecklistStatus
  to?: string
}

const route = useRoute()
const router = useRouter()

const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const loading = ref(false)
const error = ref<string | null>(null)
const health = ref<SchemaHealthResponse | null>(null)
const plugins = ref<SchemaPluginOut[]>([])
const authProviders = ref<SchemaAuthProviderOut[]>([])
const authStatus = ref<SchemaAuthStatusOut | null>(null)
const templates = ref(0)
const actions = ref(0)
const runPlans = ref(0)
const agentPresets = ref<AgentPresetSummary[]>([])
const templateDetails = ref<SchemaLoadedWorkflowTemplate[]>([])

const enabledPlugins = computed(() =>
  plugins.value.filter((plugin) => plugin.enabled_for_project !== false),
)

const activeConnections = computed(() =>
  (authStatus.value?.connections ?? []).filter((connection) => connection.revoked_at === null),
)

const connectedConnections = computed(() =>
  activeConnections.value.filter((connection) => connection.status === 'connected'),
)

const workflowAgentRequirements = computed(() =>
  templateDetails.value.flatMap((template) => template.spec.agent_requirements ?? []),
)

const workflowSkillRequirements = computed(() =>
  templateDetails.value.flatMap((template) => template.spec.skill_requirements ?? []),
)

const templatesWithAgentRequirements = computed(
  () =>
    templateDetails.value.filter((template) => (template.spec.agent_requirements ?? []).length > 0)
      .length,
)

const templatesWithStackosSkill = computed(
  () =>
    templateDetails.value.filter((template) =>
      (template.spec.skill_requirements ?? []).some(
        (skill) => skill.skill_ref === 'stackos:stackos',
      ),
    ).length,
)

const adaptationRequiredPresets = computed(
  () => agentPresets.value.filter((preset) => preset.adaptation_required !== false).length,
)

const agentDomains = computed(() =>
  Array.from(
    new Set(agentPresets.value.map((preset) => preset.domain).filter(Boolean) as string[]),
  ).sort(),
)

const skillRefs = computed(() =>
  Array.from(new Set(workflowSkillRequirements.value.map((skill) => skill.skill_ref))).sort(),
)

const checklist = computed<ChecklistItem[]>(() => {
  const id = projectId.value
  const healthReady = health.value !== null
  return [
    {
      key: 'daemon',
      label: 'Daemon',
      detail: healthReady ? `${health.value?.version ?? '-'} on 127.0.0.1` : 'Unavailable',
      status: healthReady ? 'done' : 'attention',
    },
    {
      key: 'database',
      label: 'Database',
      detail: health.value?.db_status ?? 'Unknown',
      status: health.value?.db_status === 'ok' ? 'done' : 'attention',
    },
    {
      key: 'scheduler',
      label: 'Scheduler',
      detail: health.value?.scheduler_running ? 'Running' : 'Stopped',
      status: health.value?.scheduler_running ? 'done' : 'attention',
    },
    {
      key: 'project',
      label: 'Project',
      detail: Number.isNaN(id) ? 'Not selected' : `#${id}`,
      status: Number.isNaN(id) ? 'attention' : 'done',
    },
    {
      key: 'plugins',
      label: 'Enabled plugins',
      detail: `${enabledPlugins.value.length} of ${plugins.value.length}`,
      status: enabledPlugins.value.length > 0 ? 'done' : 'todo',
      to: 'plugins',
    },
    {
      key: 'providers',
      label: 'Provider methods',
      detail: `${authProviders.value.length} available`,
      status: authProviders.value.length > 0 ? 'done' : 'attention',
      to: 'connections',
    },
    {
      key: 'connections',
      label: 'Connections',
      detail: `${connectedConnections.value.length} connected`,
      status: connectedConnections.value.length > 0 ? 'done' : 'todo',
      to: 'connections',
    },
    {
      key: 'templates',
      label: 'Workflow templates',
      detail: `${templates.value} available`,
      status: templates.value > 0 ? 'done' : 'todo',
      to: 'workflow-templates',
    },
    {
      key: 'agent-presets',
      label: 'Agent presets',
      detail:
        agentPresets.value.length > 0
          ? `${agentPresets.value.length} generic presets`
          : 'None loaded',
      status: agentPresets.value.length > 0 ? 'done' : 'todo',
      to: 'agent-presets',
    },
    {
      key: 'workflow-skills',
      label: 'Workflow skills',
      detail:
        workflowSkillRequirements.value.length > 0
          ? `${templatesWithStackosSkill.value} templates with StackOS skill`
          : 'No skill guidance',
      status:
        templates.value > 0 && templatesWithStackosSkill.value === templates.value
          ? 'done'
          : 'todo',
      to: 'workflow-templates',
    },
    {
      key: 'actions',
      label: 'Action contracts',
      detail: `${actions.value} callable contracts`,
      status: actions.value > 0 ? 'done' : 'attention',
      to: 'operations',
    },
    {
      key: 'runs',
      label: 'Run plans',
      detail: runPlans.value > 0 ? 'Existing run history' : 'None yet',
      status: runPlans.value > 0 ? 'done' : 'todo',
      to: 'runs',
    },
  ]
})

const readyItems = computed(() => checklist.value.filter((item) => item.status === 'done'))
const attentionItems = computed(() => checklist.value.filter((item) => item.status === 'attention'))
const todoItems = computed(() => checklist.value.filter((item) => item.status === 'todo'))
const readiness = computed(() =>
  Math.round((readyItems.value.length / checklist.value.length) * 100),
)

async function fetchOr<T>(path: string, fallback: T): Promise<T> {
  try {
    return await apiFetch<T>(path)
  } catch {
    return fallback
  }
}

async function loadTemplateDetails(
  id: number,
  summaries: SchemaWorkflowTemplateSummaryOut[],
): Promise<SchemaLoadedWorkflowTemplate[]> {
  const responses = await Promise.allSettled(
    summaries.map((template) => {
      const params = new URLSearchParams()
      if (template.plugin_slug) params.set('plugin_slug', template.plugin_slug)
      const suffix = params.toString() ? `?${params.toString()}` : ''
      return apiFetch<SchemaLoadedWorkflowTemplate>(
        `/api/v1/projects/${id}/workflow-templates/${encodeURIComponent(template.key)}${suffix}`,
      )
    }),
  )
  return responses.flatMap((response) =>
    response.status === 'fulfilled' ? [response.value] : [],
  )
}

async function load(): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  loading.value = true
  error.value = null
  try {
    const id = projectId.value
    const [
      healthRow,
      pluginRows,
      providerRows,
      statusRow,
      templateRows,
      actionRows,
      agentPresetRows,
      runPage,
    ] =
      await Promise.all([
        fetchOr<SchemaHealthResponse | null>('/api/v1/health', null),
        apiFetch<SchemaPluginOut[]>(`/api/v1/plugins?project_id=${id}`),
        fetchOr<SchemaAuthProviderOut[]>('/api/v1/auth/providers', []),
        fetchOr<SchemaAuthStatusOut>(`/api/v1/projects/${id}/auth/status`, {
          project_id: id,
          provider_key: null,
          providers: [],
          connections: [],
        }),
        fetchOr<SchemaWorkflowTemplateListOut>(`/api/v1/projects/${id}/workflow-templates`, {
          templates: [],
          include_shadowed: false,
        }),
        fetchOr<SchemaActionOut[]>(`/api/v1/actions?project_id=${id}`, []),
        callOperation<AgentPresetListOut>('agentPreset.list', { project_id: id }),
        fetchOr<SchemaPageResponseRunOut>(`/api/v1/projects/${id}/runs?kind=run-plan&limit=1`, {
          items: [],
          next_cursor: null,
          total_estimate: 0,
        }),
      ])
    const detailRows = await loadTemplateDetails(id, templateRows.templates)
    health.value = healthRow
    plugins.value = pluginRows
    authProviders.value = providerRows
    authStatus.value = statusRow
    templates.value = templateRows.templates.length
    actions.value = actionRows.length
    agentPresets.value = agentPresetRows.presets
    templateDetails.value = detailRows
    runPlans.value = runPage.total_estimate ?? runPage.items.length
  } catch (err) {
    error.value = formatApiError(err, 'failed to load setup status')
  } finally {
    loading.value = false
  }
}

function go(path: string): void {
  void router.push(`/projects/${projectId.value}/${path}`)
}

function statusTone(status: ChecklistStatus): 'success' | 'warning' | 'danger' {
  if (status === 'done') return 'success'
  if (status === 'todo') return 'warning'
  return 'danger'
}

function statusLabel(status: ChecklistStatus): string {
  if (status === 'done') return 'Ready'
  if (status === 'todo') return 'Open'
  return 'Check'
}

onMounted(load)
watch(projectId, load)
</script>

<template>
  <div class="space-y-4">
    <UiCallout v-if="error" tone="danger">
      {{ error }}
    </UiCallout>

    <div class="grid gap-3 md:grid-cols-4">
      <UiMetricCard label="Readiness" :value="`${readiness}%`" />
      <UiMetricCard label="Ready" :value="readyItems.length" />
      <UiMetricCard label="Open" :value="todoItems.length" />
      <UiMetricCard label="Check" :value="attentionItems.length" />
    </div>

    <UiPanel class="p-4">
      <UiSectionHeader title="Setup Status" as="h3">
        <template #actions>
          <UiBadge
            :tone="
              attentionItems.length > 0 ? 'danger' : todoItems.length > 0 ? 'warning' : 'success'
            "
          >
            {{ readyItems.length }} / {{ checklist.length }}
          </UiBadge>
          <UiButton size="sm" variant="secondary" :disabled="loading" @click="load">
            Refresh
          </UiButton>
        </template>
      </UiSectionHeader>

      <UiProgressBar
        :value="readyItems.length"
        :max="checklist.length"
        :tone="attentionItems.length > 0 ? 'danger' : todoItems.length > 0 ? 'warning' : 'success'"
        show-label
        aria-label="Setup readiness"
      />

      <div class="mt-4 grid gap-2 md:grid-cols-2">
        <div
          v-for="item in checklist"
          :key="item.key"
          class="flex min-h-16 items-center justify-between gap-3 rounded-md border border-subtle bg-bg-surface-alt px-3 py-2"
        >
          <div class="min-w-0">
            <div class="flex items-center gap-2">
              <UiBadge :tone="statusTone(item.status)" dot>
                {{ statusLabel(item.status) }}
              </UiBadge>
              <p class="text-sm font-medium text-fg-strong">
                {{ item.label }}
              </p>
            </div>
            <p class="mt-1 truncate text-xs text-fg-muted">
              {{ item.detail }}
            </p>
          </div>
          <UiButton v-if="item.to" class="shrink-0" size="sm" variant="ghost" @click="go(item.to)">
            Open
          </UiButton>
        </div>
      </div>
    </UiPanel>

    <div class="grid gap-4 lg:grid-cols-2">
      <UiPanel class="p-4">
        <UiSectionHeader title="Agent Presets" as="h3">
          <template #actions>
            <UiBadge>{{ agentPresets.length }}</UiBadge>
            <UiBadge tone="warning">{{ adaptationRequiredPresets }} adapt</UiBadge>
          </template>
        </UiSectionHeader>
        <dl class="grid gap-3 text-sm md:grid-cols-3">
          <div class="min-w-0">
            <dt class="text-xs text-fg-muted">Available</dt>
            <dd class="font-medium">{{ agentPresets.length }}</dd>
          </div>
          <div class="min-w-0">
            <dt class="text-xs text-fg-muted">Domains</dt>
            <dd class="font-medium">{{ agentDomains.length }}</dd>
          </div>
          <div class="min-w-0">
            <dt class="text-xs text-fg-muted">Adaptation</dt>
            <dd class="font-medium">{{ adaptationRequiredPresets }} required</dd>
          </div>
        </dl>
        <div
          v-if="agentDomains.length"
          class="mt-4 flex flex-wrap gap-2"
        >
          <UiBadge
            v-for="domain in agentDomains"
            :key="domain"
            tone="accent"
          >
            {{ domain }}
          </UiBadge>
        </div>
      </UiPanel>

      <UiPanel class="p-4">
        <UiSectionHeader title="Skills" as="h3">
          <template #actions>
            <UiBadge>{{ workflowSkillRequirements.length }}</UiBadge>
            <UiBadge tone="info">{{ templatesWithStackosSkill }} templates</UiBadge>
          </template>
        </UiSectionHeader>
        <dl class="grid gap-3 text-sm md:grid-cols-3">
          <div class="min-w-0">
            <dt class="text-xs text-fg-muted">Skill refs</dt>
            <dd class="font-medium">{{ skillRefs.length }}</dd>
          </div>
          <div class="min-w-0">
            <dt class="text-xs text-fg-muted">Template coverage</dt>
            <dd class="font-medium">{{ templatesWithStackosSkill }} / {{ templates }}</dd>
          </div>
          <div class="min-w-0">
            <dt class="text-xs text-fg-muted">Workflow roles</dt>
            <dd class="font-medium">
              {{ workflowAgentRequirements.length }} roles on
              {{ templatesWithAgentRequirements }} templates
            </dd>
          </div>
        </dl>
        <div
          v-if="skillRefs.length"
          class="mt-4 flex flex-wrap gap-2"
        >
          <UiBadge
            v-for="skill in skillRefs"
            :key="skill"
            tone="info"
          >
            {{ skill }}
          </UiBadge>
        </div>
      </UiPanel>
    </div>

    <div class="grid gap-4 lg:grid-cols-3">
      <UiPanel class="p-4">
        <UiSectionHeader title="Runtime" as="h3" />
        <dl class="grid gap-3 text-sm">
          <div class="flex items-center justify-between gap-3">
            <dt class="text-fg-muted">Version</dt>
            <dd class="font-medium">{{ health?.version ?? '-' }}</dd>
          </div>
          <div class="flex items-center justify-between gap-3">
            <dt class="text-fg-muted">Database</dt>
            <dd class="font-medium">{{ health?.db_status ?? '-' }}</dd>
          </div>
          <div class="flex items-center justify-between gap-3">
            <dt class="text-fg-muted">Scheduler</dt>
            <dd class="font-medium">{{ health?.scheduler_running ? 'running' : '-' }}</dd>
          </div>
        </dl>
      </UiPanel>

      <UiPanel class="p-4">
        <UiSectionHeader title="Project Surface" as="h3" />
        <dl class="grid gap-3 text-sm">
          <div class="flex items-center justify-between gap-3">
            <dt class="text-fg-muted">Installed plugins</dt>
            <dd class="font-medium">{{ plugins.length }}</dd>
          </div>
          <div class="flex items-center justify-between gap-3">
            <dt class="text-fg-muted">Templates</dt>
            <dd class="font-medium">{{ templates }}</dd>
          </div>
          <div class="flex items-center justify-between gap-3">
            <dt class="text-fg-muted">Actions</dt>
            <dd class="font-medium">{{ actions }}</dd>
          </div>
        </dl>
      </UiPanel>

      <UiPanel class="p-4">
        <UiSectionHeader title="Connections" as="h3" />
        <dl class="grid gap-3 text-sm">
          <div class="flex items-center justify-between gap-3">
            <dt class="text-fg-muted">Providers</dt>
            <dd class="font-medium">{{ authProviders.length }}</dd>
          </div>
          <div class="flex items-center justify-between gap-3">
            <dt class="text-fg-muted">Active</dt>
            <dd class="font-medium">{{ activeConnections.length }}</dd>
          </div>
          <div class="flex items-center justify-between gap-3">
            <dt class="text-fg-muted">Connected</dt>
            <dd class="font-medium">{{ connectedConnections.length }}</dd>
          </div>
        </dl>
      </UiPanel>
    </div>
  </div>
</template>
