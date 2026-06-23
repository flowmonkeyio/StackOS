<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  UiBadge,
  UiButton,
  UiCallout,
  UiCard,
  UiDescriptionList,
  UiIcon,
  UiMetricCard,
  UiProgressBar,
} from '@/components/ui'
import type {
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

interface WorkspaceBindingSummary {
  id: number
  project_id: number
  repo_fingerprint?: string | null
  normalized_repo_name?: string | null
  last_known_root?: string | null
  framework?: string | null
  content_model_json?: Record<string, unknown> | null
}

interface OperationItems<T> {
  items: T[]
}

interface OperationListSummary {
  items: unknown[]
}

interface ActionListSummary {
  count: number
  hidden_count?: number
}

interface IntegrationListSummary {
  count: number
  connected_count: number
  exposed_action_count: number
  executable_action_count: number
  hidden_action_count: number
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
const hiddenIntegrationActions = ref(0)
const integrations = ref(0)
const connectedIntegrations = ref(0)
const operationContracts = ref(0)
const runPlans = ref(0)
const agentPresets = ref<AgentPresetSummary[]>([])
const templateDetails = ref<SchemaLoadedWorkflowTemplate[]>([])
const workspaceBindings = ref<WorkspaceBindingSummary[]>([])

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

const workflowSkillPresetRequirements = computed(() =>
  templateDetails.value.flatMap((template) => template.spec.skill_preset_requirements ?? []),
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

const templatesWithSkillPresets = computed(
  () =>
    templateDetails.value.filter(
      (template) => (template.spec.skill_preset_requirements ?? []).length > 0,
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

const skillPresetRefs = computed(() =>
  Array.from(
    new Set(workflowSkillPresetRequirements.value.map((preset) => preset.skill_preset_ref)),
  ).sort(),
)

const workspaceBinding = computed(
  () => workspaceBindings.value.find((binding) => binding.project_id === projectId.value) ?? null,
)

const workspaceProfileMissing = computed(() => {
  const binding = workspaceBinding.value
  if (!binding) return ['workspace binding']
  const missing: string[] = []
  if (!binding.framework) missing.push('framework')
  if (!binding.content_model_json) missing.push('content model')
  return missing
})

const workspaceProfileDetail = computed(() => {
  const binding = workspaceBinding.value
  if (!binding) return 'No workspace binding'
  if (workspaceProfileMissing.value.length === 0) {
    return binding.framework ? `${binding.framework} profile` : 'Configured'
  }
  return `Missing ${workspaceProfileMissing.value.join(', ')}`
})

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
      key: 'workspace-profile',
      label: 'Workspace profile',
      detail: workspaceProfileDetail.value,
      status:
        workspaceBinding.value === null
          ? 'attention'
          : workspaceProfileMissing.value.length === 0
            ? 'done'
            : 'todo',
      to: 'operations',
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
      key: 'integrations',
      label: 'Integrations',
      detail:
        hiddenIntegrationActions.value > 0
          ? `${connectedIntegrations.value} connected, ${hiddenIntegrationActions.value} actions hidden`
          : `${connectedIntegrations.value} connected of ${integrations.value}`,
      status:
        integrations.value === 0
          ? 'attention'
          : hiddenIntegrationActions.value > 0
            ? 'todo'
            : 'done',
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
      key: 'workflow-skill-presets',
      label: 'Workflow skill presets',
      detail:
        workflowSkillPresetRequirements.value.length > 0
          ? `${templatesWithSkillPresets.value} templates, ${workflowSkillPresetRequirements.value.length} presets`
          : 'No operating presets',
      status: workflowSkillPresetRequirements.value.length > 0 ? 'done' : 'todo',
      to: 'workflow-templates',
    },
    {
      key: 'operations',
      label: 'Operation contracts',
      detail: `${operationContracts.value} registered`,
      status: operationContracts.value > 0 ? 'done' : 'attention',
      to: 'operations',
    },
    {
      key: 'actions',
      label: 'Available actions',
      detail:
        hiddenIntegrationActions.value > 0
          ? `${actions.value} visible, ${hiddenIntegrationActions.value} hidden until setup`
          : `${actions.value} visible`,
      status: actions.value > 0 ? 'done' : 'attention',
      to: 'plugins',
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

// Presentational split of the checklist into two stacked column lists.
const checklistColumns = computed(() => {
  const midpoint = Math.ceil(checklist.value.length / 2)
  return [checklist.value.slice(0, midpoint), checklist.value.slice(midpoint)]
})

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
      operationRows,
      agentPresetRows,
      bindingRows,
      runPage,
      actionList,
      integrationRows,
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
        fetchOr<OperationListSummary>('/api/v1/operations?surface=mcp', {
          items: [],
        }),
        callOperation<AgentPresetListOut>('agentPreset.list', { project_id: id }),
        callOperation<OperationItems<WorkspaceBindingSummary>>('workspace.listBindings', {
          project_id: id,
        }),
        fetchOr<SchemaPageResponseRunOut>(`/api/v1/projects/${id}/runs?kind=run-plan&limit=1`, {
          items: [],
          next_cursor: null,
          total_estimate: 0,
        }),
        callOperation<ActionListSummary>('action.list', { project_id: id }),
        callOperation<IntegrationListSummary>('integration.list', { project_id: id }),
      ])
    const detailRows = await loadTemplateDetails(id, templateRows.templates)
    health.value = healthRow
    plugins.value = pluginRows
    authProviders.value = providerRows
    authStatus.value = statusRow
    templates.value = templateRows.templates.length
    actions.value = actionList.count
    hiddenIntegrationActions.value = integrationRows.hidden_action_count
    integrations.value = integrationRows.count
    connectedIntegrations.value = integrationRows.connected_count
    operationContracts.value = operationRows.items.length
    agentPresets.value = agentPresetRows.presets
    workspaceBindings.value = bindingRows.items
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

// Status icon treatment per checklist state — ready, attention, open.
const STATUS_ICONS: Record<ChecklistStatus, { icon: string; class: string }> = {
  done: { icon: 'check-circle', class: 'text-success' },
  attention: { icon: 'alert-triangle', class: 'text-warning' },
  todo: { icon: 'clock', class: 'text-fg-subtle' },
}

function statusLabel(status: ChecklistStatus): string {
  if (status === 'done') return 'Ready'
  if (status === 'todo') return 'Open'
  return 'Check'
}

onMounted(load)
</script>

<template>
  <div class="space-y-5">
    <UiCallout
      v-if="error"
      tone="danger"
    >
      {{ error }}
    </UiCallout>

    <div class="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      <UiMetricCard
        label="Readiness"
        :value="`${readiness}%`"
      />
      <UiMetricCard
        label="Ready"
        :value="readyItems.length"
      />
      <UiMetricCard
        label="Open"
        :value="todoItems.length"
      />
      <UiMetricCard
        label="Check"
        :value="attentionItems.length"
      />
    </div>

    <UiCard section>
      <template #header>
        <h3 class="t-h3 text-fg-strong">
          Setup status
        </h3>
        <div class="flex items-center gap-2">
          <UiBadge
            :tone="
              attentionItems.length > 0 ? 'danger' : todoItems.length > 0 ? 'warning' : 'success'
            "
          >
            {{ readyItems.length }} / {{ checklist.length }}
          </UiBadge>
          <UiButton
            size="sm"
            variant="secondary"
            :disabled="loading"
            @click="load"
          >
            Refresh
          </UiButton>
        </div>
      </template>

      <UiProgressBar
        :value="readyItems.length"
        :max="checklist.length"
        :tone="attentionItems.length > 0 ? 'danger' : todoItems.length > 0 ? 'warning' : 'success'"
        show-label
        aria-label="Setup readiness"
      />

      <UiCallout
        v-if="workspaceBinding && workspaceProfileMissing.length > 0"
        class="mt-4"
        tone="warning"
        title="Workspace profile incomplete"
      >
        Adaptation hints — project tools are usable; future agents are missing
        {{ workspaceProfileMissing.join(', ') }} guidance.
        <template #actions>
          <UiButton
            size="sm"
            variant="secondary"
            aria-label="Open workspace profile operation"
            @click="go('operations')"
          >
            Open operation
          </UiButton>
        </template>
      </UiCallout>

      <div class="mt-3 grid gap-x-6 md:grid-cols-2">
        <ul
          v-for="(column, columnIndex) in checklistColumns"
          :key="columnIndex"
          class="divide-y divide-border-subtle"
        >
          <li
            v-for="item in column"
            :key="item.key"
            class="flex items-center gap-3 py-2.5"
          >
            <span
              class="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-bg-sunken"
            >
              <UiIcon
                :name="STATUS_ICONS[item.status].icon"
                :class="['h-4 w-4', STATUS_ICONS[item.status].class]"
                :aria-label="statusLabel(item.status)"
              />
            </span>
            <div class="min-w-0 flex-1">
              <p class="text-sm font-medium text-fg-strong">
                {{ item.label }}
              </p>
              <p class="truncate text-xs text-fg-muted">
                {{ item.detail }}
              </p>
            </div>
            <UiButton
              v-if="item.to"
              class="shrink-0"
              size="sm"
              variant="ghost"
              icon-right="arrow-right"
              :aria-label="`Open ${item.label}`"
              @click="go(item.to)"
            >
              Open
            </UiButton>
          </li>
        </ul>
      </div>
    </UiCard>

    <div class="grid gap-5 lg:grid-cols-2">
      <UiCard section>
        <template #header>
          <h3 class="t-h3 text-fg-strong">
            Agent presets
          </h3>
          <div class="flex items-center gap-2">
            <UiBadge>{{ agentPresets.length }}</UiBadge>
            <UiBadge tone="warning">
              {{ adaptationRequiredPresets }} adapt
            </UiBadge>
          </div>
        </template>
        <UiDescriptionList
          layout="grid"
          :columns="3"
          :items="[
            { label: 'Available', value: agentPresets.length },
            { label: 'Domains', value: agentDomains.length },
            { label: 'Adaptation', value: `${adaptationRequiredPresets} required` },
          ]"
        />
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
      </UiCard>

      <UiCard section>
        <template #header>
          <h3 class="t-h3 text-fg-strong">
            Skills & skill presets
          </h3>
          <div class="flex items-center gap-2">
            <UiBadge>{{ workflowSkillRequirements.length }}</UiBadge>
            <UiBadge tone="accent">
              {{ workflowSkillPresetRequirements.length }} presets
            </UiBadge>
            <UiBadge tone="info">
              {{ templatesWithStackosSkill }} templates
            </UiBadge>
          </div>
        </template>
        <UiDescriptionList
          layout="grid"
          :columns="3"
          :items="[
            { label: 'Skill refs', value: skillRefs.length },
            { label: 'Template coverage', value: `${templatesWithStackosSkill} / ${templates}` },
            {
              label: 'Workflow roles',
              value: `${workflowAgentRequirements.length} roles on ${templatesWithAgentRequirements} templates`,
            },
          ]"
        />
        <div
          v-if="skillPresetRefs.length"
          class="mt-4 flex flex-wrap gap-2"
        >
          <UiBadge
            v-for="preset in skillPresetRefs"
            :key="preset"
            tone="accent"
          >
            {{ preset }}
          </UiBadge>
        </div>
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
      </UiCard>
    </div>

    <div class="grid gap-5 lg:grid-cols-3">
      <UiCard section>
        <template #header>
          <h3 class="t-h3 text-fg-strong">
            Runtime
          </h3>
        </template>
        <UiDescriptionList
          numeric
          :items="[
            { label: 'Version', value: health?.version ?? '-' },
            { label: 'Database', value: health?.db_status ?? '-' },
            { label: 'Scheduler', value: health?.scheduler_running ? 'running' : '-' },
          ]"
        />
      </UiCard>

      <UiCard section>
        <template #header>
          <h3 class="t-h3 text-fg-strong">
            Project surface
          </h3>
        </template>
        <UiDescriptionList
          numeric
          :items="[
            { label: 'Installed plugins', value: plugins.length },
            { label: 'Templates', value: templates },
            { label: 'Actions', value: actions },
          ]"
        />
      </UiCard>

      <UiCard section>
        <template #header>
          <h3 class="t-h3 text-fg-strong">
            Connections
          </h3>
        </template>
        <UiDescriptionList
          numeric
          :items="[
            { label: 'Providers', value: authProviders.length },
            { label: 'Active', value: activeConnections.length },
            { label: 'Connected', value: connectedConnections.length },
          ]"
        />
      </UiCard>
    </div>
  </div>
</template>
