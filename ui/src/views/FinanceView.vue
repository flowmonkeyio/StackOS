<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type {
  SchemaFinanceAccessOut,
  SchemaFinanceContentOut,
  SchemaFinanceProjectionOut,
  SchemaFinanceStatusOut,
  SchemaFinanceWorkspaceSelectInput,
  SchemaLocalAgentChatMessageOut,
} from '@/api'
import DataTable from '@/components/DataTable.vue'
import MarkdownView from '@/components/MarkdownView.vue'
import SubNav from '@/components/SubNav.vue'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import FinanceFilters from '@/components/finance/FinanceFilters.vue'
import FinanceRecordPanel from '@/components/finance/FinanceRecordPanel.vue'
import {
  UiButton,
  UiCallout,
  UiCheckbox,
  UiFormField,
  UiInput,
  UiMetricCard,
  UiPageShell,
  UiSectionHeader,
  UiSelect,
} from '@/components/ui'
import type { DataTableColumn } from '@/components/types'
import { useProjectRouteScope } from '@/composables/useProjectRouteScope'
import { useProjectScopedLoader } from '@/composables/useProjectScopedLoader'
import { callOperation } from '@/lib/operations'
import {
  contentBlob,
  contentText,
  formatMoney,
  metricText,
  newIntentId,
  object,
  objects,
  refs,
  snapshotRef,
  text,
  timeframe,
  type FinanceQuery,
} from '@/lib/finance'

const route = useRoute(),
  router = useRouter()
const { projectId } = useProjectRouteScope(route)
const section = ref('overview'),
  loading = ref(false),
  error = ref(''),
  message = ref('')
const accessRows = ref<SchemaFinanceStatusOut['access']>([]),
  access = ref<SchemaFinanceAccessOut | null>(null)
const projection = ref<SchemaFinanceProjectionOut | null>(null)
const query = ref<FinanceQuery>(initialQuery())
const configuring = ref(false),
  root = ref(''),
  workspace = ref('finance:local'),
  allowCorrection = ref(true),
  allowExport = ref(true)
const panelOpen = ref(false),
  recordRef = ref<string | null>(null),
  selectedRow = ref<Record<string, unknown>>({}),
  includedRefs = ref<string[]>([])
const report = ref<SchemaFinanceContentOut | null>(null),
  reportURL = ref(''),
  reportText = ref(''),
  reportLoading = ref(false)
const requests = ref<Record<string, unknown>[]>([]),
  selectedRequest = ref<Record<string, unknown> | null>(null)
const analysis = ref<SchemaFinanceContentOut | null>(null),
  analysisLoading = ref(false),
  analysisKind = ref('opportunities')
let active = true,
  scopeSequence = 0,
  querySequence = 0,
  reportSequence = 0,
  analysisSequence = 0
function initialQuery(): FinanceQuery {
  return {
    ...timeframe('month'),
    recognition_basis: 'cash' as FinanceQuery['recognition_basis'],
    date_basis: 'transaction' as FinanceQuery['date_basis'],
    timezone: 'UTC',
    kind: 'all' as FinanceQuery['kind'],
    scope: 'business' as FinanceQuery['scope'],
    sort: 'date' as FinanceQuery['sort'],
    descending: false,
    offset: 0,
    limit: 50,
  }
}
const base = () => ({
  project_id: projectId.value,
  workspace_ref: access.value?.workspace_ref,
  access_generation: access.value?.generation,
})
type DisplayRow = SchemaFinanceProjectionOut['rows'][number] & { id: string }
const rows = computed<DisplayRow[]>(() =>
  (projection.value?.rows ?? []).map((row) => ({ ...row, id: text(row.record_ref) })),
)
const columns: DataTableColumn<(typeof rows.value)[number]>[] = [
  { key: 'date', label: 'Date', sortable: true, format: (value) => text(value) || 'Undated' },
  {
    key: 'counterparty',
    label: 'Vendor / counterparty',
    format: (value) => text(value) || 'Not supplied',
  },
  { key: 'kind', label: 'Type' },
  { key: 'amount', label: 'Amount', sortable: true, format: formatMoney },
  { key: 'category_ref', label: 'Category', format: (value) => text(value) || 'Unassigned' },
  { key: 'status', label: 'State' },
  { key: 'needs_review', label: 'Review', format: (value) => (value ? 'Needs review' : 'Sourced') },
]
const groups = computed(() =>
  Object.entries(object(projection.value?.breakdowns)).map(([dimension, value]) => ({
    dimension,
    rows: objects(value),
  })),
)
const reportAllowed = computed(() => access.value?.modes.includes('export') === true)
const statusText = computed(() => {
  const state = analysis.value?.state
  if (state === 'current') return 'Reviewed analysis · current source'
  if (state === 'stale') return 'Historical analysis · source changed'
  if (state === 'run-started')
    return selectedRequest.value?.run_plan_id
      ? 'Running in the linked agent run'
      : 'Awaiting a linked run'
  return (
    (
      {
        awaiting_agent: 'Awaiting agent — a host must claim this request',
        claimed: 'Claimed — awaiting execution',
        'run-created': 'Run prepared — awaiting execution',
        responded: 'Awaiting reviewed publication',
        failed: 'Analysis failed',
        ignored: 'Request ignored',
        claim_expired: 'Agent claim expired',
        historical_access: 'Historical workspace authorization',
        result_unavailable: 'Reviewed result unavailable',
      } as Record<string, string>
    )[state || ''] || (selectedRequest.value ? 'Awaiting agent status' : 'No analysis requested')
  )
})
function releaseReport() {
  reportSequence++
  if (reportURL.value) URL.revokeObjectURL(reportURL.value)
  reportURL.value = ''
  report.value = null
  reportText.value = ''
  reportLoading.value = false
}
function clearScope() {
  scopeSequence++
  querySequence++
  analysisSequence++
  releaseReport()
  access.value = null
  accessRows.value = []
  projection.value = null
  requests.value = []
  selectedRequest.value = null
  analysis.value = null
  analysisLoading.value = false
  panelOpen.value = false
  selectedRow.value = {}
  includedRefs.value = []
  root.value = ''
  error.value = ''
  message.value = ''
  configuring.value = false
  loading.value = false
}
async function loadStatus() {
  const sequence = ++scopeSequence,
    id = projectId.value
  querySequence++
  analysisSequence++
  analysis.value = null
  loading.value = true
  error.value = ''
  projection.value = null
  panelOpen.value = false
  releaseReport()
  try {
    const response = await callOperation<{ data: SchemaFinanceStatusOut }>(
      'financeWorkspace.status',
      { project_id: id, response_mode: 'compact' },
    )
    if (!active || sequence !== scopeSequence || id !== projectId.value) return
    accessRows.value = response.data.access
    const prior = access.value?.workspace_ref
    const chosen =
      response.data.access.find(
        (row) => row.workspace_ref === prior && row.readiness === 'ready',
      ) || response.data.access.find((row) => row.readiness === 'ready')
    access.value = chosen ? (chosen as SchemaFinanceAccessOut) : null
    if (!access.value) {
      error.value =
        'Finance workspace unavailable. Select an existing external master or repair its authorization and source.'
      return
    }
    await loadQuery(query.value)
    if (active && sequence === scopeSequence) await loadRequests()
  } catch {
    if (active && sequence === scopeSequence) {
      access.value = null
      projection.value = null
      error.value = 'Finance workspace unavailable. Refresh or repair its authorization.'
    }
  } finally {
    if (active && sequence === scopeSequence) loading.value = false
  }
}
async function loadQuery(value: FinanceQuery) {
  if (!access.value) return
  const sequence = ++querySequence,
    identity = JSON.stringify(base())
  query.value = { ...value }
  loading.value = true
  error.value = ''
  projection.value = null
  panelOpen.value = false
  includedRefs.value = []
  releaseReport()
  const argumentsJson: Record<string, unknown> = { ...value, ...base(), response_mode: 'compact' }
  for (const key of [
    'currency',
    'account_ref',
    'category_ref',
    'counterparty_ref',
    'q',
    'external_project_ref',
  ])
    if (argumentsJson[key] === '') delete argumentsJson[key]
  if (argumentsJson.scope === 'business') delete argumentsJson.external_project_ref
  try {
    const response = await callOperation<{ data: SchemaFinanceProjectionOut }>(
      'finance.query',
      argumentsJson,
    )
    if (!active || sequence !== querySequence || identity !== JSON.stringify(base())) return
    projection.value = response.data
  } catch {
    if (active && sequence === querySequence)
      error.value =
        'Selected finance data is unavailable. Check dates, basis, sources and current workspace authorization.'
  } finally {
    if (active && sequence === querySequence) loading.value = false
  }
}
const { refresh } = useProjectScopedLoader({
  projectId,
  load: loadStatus,
  immediate: false,
  onScopeExit: () => {
    active = false
    clearScope()
  },
})
watch(
  projectId,
  () => {
    clearScope()
    query.value = initialQuery()
    void refresh()
  },
  { immediate: true, flush: 'sync' },
)
function selectWorkspace(value: string | number | null) {
  const next = accessRows.value.find(
    (row) => row.workspace_ref === value && row.readiness === 'ready',
  )
  querySequence++
  analysisSequence++
  releaseReport()
  projection.value = null
  panelOpen.value = false
  analysis.value = null
  selectedRequest.value = null
  requests.value = []
  access.value = next ? (next as SchemaFinanceAccessOut) : null
  if (access.value) {
    void loadQuery(query.value)
    void loadRequests()
  }
}
async function authorizeWorkspace() {
  if (!root.value.trim() || !workspace.value.trim()) {
    error.value = 'Supply the existing external finance folder and a workspace reference.'
    return
  }
  const sequence = scopeSequence
  error.value = ''
  try {
    const modes: SchemaFinanceWorkspaceSelectInput['modes'] = [
      'read' as SchemaFinanceWorkspaceSelectInput['modes'][number],
    ]
    if (allowCorrection.value)
      modes.push('correct' as SchemaFinanceWorkspaceSelectInput['modes'][number])
    if (allowExport.value)
      modes.push('export' as SchemaFinanceWorkspaceSelectInput['modes'][number])
    await callOperation('financeWorkspace.select', {
      project_id: projectId.value,
      workspace_ref: workspace.value,
      root: root.value,
      modes,
      authority_ref: 'local-user:operator',
      idempotency_key: newIntentId(),
      response_mode: 'compact',
    })
    if (active && sequence === scopeSequence) {
      root.value = ''
      configuring.value = false
      await refresh()
    }
  } catch {
    if (active && sequence === scopeSequence)
      error.value =
        'Workspace selection failed. Use an existing supported master within authorized local storage; no files were initialized here.'
  }
}
async function revoke() {
  if (!access.value) return
  const selected = {
    ...base(),
    expected_generation: access.value.generation,
    response_mode: 'compact',
  }
  const sequence = scopeSequence
  error.value = ''
  try {
    await callOperation('financeWorkspace.revoke', selected)
    if (active && sequence === scopeSequence) {
      clearScope()
      await refresh()
    }
  } catch {
    if (active && sequence === scopeSequence)
      error.value =
        'Revocation could not be verified. Refresh authorization before further changes.'
  }
}
function inspect(refValue: string, row: Record<string, unknown> = {}) {
  recordRef.value = refValue
  selectedRow.value = row
  panelOpen.value = true
}
function createEntry() {
  recordRef.value = null
  selectedRow.value = {}
  panelOpen.value = true
}
function recordSaved() {
  message.value = 'Correction saved and read back. Refreshing current source.'
  void refresh()
}
function drill(dimension: string, group: Record<string, unknown>) {
  includedRefs.value = refs(group.included_record_refs)
  if (text(group.label) === 'Unknown/unassigned') return
  const patch: Partial<FinanceQuery> =
    dimension === 'project'
      ? {
          scope: 'external-project' as FinanceQuery['scope'],
          external_project_ref: text(group.label),
        }
      : { [`${dimension}_ref`]: text(group.label) }
  section.value = 'records'
  void loadQuery({ ...query.value, ...patch, offset: 0 })
}
function currencyAmount(group: Record<string, unknown>, field: string) {
  return formatMoney({
    minor: group[field],
    currency: group.currency,
    exponent: projection.value?.metrics_by_currency.find((m) => m.currency === group.currency)
      ?.exponent,
  })
}
function amountRows(
  value: unknown,
): (Record<string, unknown> & { id: number; spend: string; revenue: string })[] {
  return objects(value).map((row, index) => ({
    ...row,
    id: index,
    spend: currencyAmount(row, 'spend_minor'),
    revenue: currencyAmount(row, 'revenue_minor'),
  }))
}
async function loadReport(format: 'markdown' | 'csv' | 'json') {
  if (!access.value || !projection.value || !reportAllowed.value) return
  releaseReport()
  const sequence = reportSequence,
    identity = JSON.stringify(base())
  reportLoading.value = true
  error.value = ''
  try {
    const value = await callOperation<SchemaFinanceContentOut>('finance.report.render', {
      ...projection.value.query,
      ...base(),
      format,
      report_kind: 'period',
      expected_snapshot: snapshotRef(projection.value.snapshot),
      response_mode: 'raw',
    })
    if (!active || sequence !== reportSequence || identity !== JSON.stringify(base())) return
    report.value = value
    if (value.state !== 'current' || !value.content_base64) {
      error.value = 'Report unavailable for this source and authorization.'
      return
    }
    const blob = contentBlob(value.content_base64, value.content_type || 'text/plain')
    reportURL.value = URL.createObjectURL(blob)
    reportText.value = contentText(value.content_base64)
  } catch {
    if (active && sequence === reportSequence)
      error.value = 'Report unavailable or source changed. Refresh before exporting.'
  } finally {
    if (active && sequence === reportSequence) reportLoading.value = false
  }
}
async function loadRequests() {
  if (!access.value) return
  const sequence = analysisSequence,
    identity = JSON.stringify(base())
  try {
    const page = await callOperation<{ items: Record<string, unknown>[] }>('agentRequest.list', {
      project_id: projectId.value,
      claimable: false,
      limit: 100,
      response_mode: 'raw',
    })
    if (!active || sequence !== analysisSequence || identity !== JSON.stringify(base())) return
    requests.value = page.items.filter(
      (row) =>
        object(object(row.metadata_json).metadata_json).workspace_ref ===
        access.value?.workspace_ref,
    )
    const requestedId = text(route.query.analysis)
    const requested = requests.value.find((row) => String(row.id) === requestedId)
    if (requested) {
      selectedRequest.value = requested
      void refreshAnalysis()
    }
  } catch {
    if (active && sequence === analysisSequence)
      message.value =
        'Agent queue unavailable. Existing request links remain inspectable in Agent requests.'
  }
}
async function requestAnalysis() {
  if (!access.value || !projection.value || loading.value || !reportAllowed.value) return
  if (text(projection.value.query.q) !== '') {
    error.value =
      'Private search text cannot be included in an agent request. Clear search and apply filters before requesting analysis; your current search and timeframe remain selected.'
    return
  }
  const sequence = ++analysisSequence,
    identity = JSON.stringify(base())
  analysisLoading.value = true
  error.value = ''
  analysis.value = null
  const intent = newIntentId(),
    bound = snapshotRef(projection.value.snapshot),
    originalQuery = { ...projection.value.query }
  delete originalQuery.q
  try {
    const response = await callOperation<{ data: SchemaLocalAgentChatMessageOut }>(
      'localAgentChat.createMessage',
      {
        project_id: projectId.value,
        thread_key: 'finance-management',
        message_key: intent,
        sender_ref: 'local-user:operator',
        direction: 'inbound',
        create_request: true,
        text: 'Analyze the selected finance snapshot and return independently reviewed findings.',
        request_title: 'Finance management analysis',
        metadata_json: {
          workspace_ref: access.value.workspace_ref,
          access_generation: access.value.generation,
          source_snapshot: bound,
          query: originalQuery,
          analysis_kind: analysisKind.value,
          record_refs: [],
          source_refs: [],
          intent_key: intent,
        },
        response_mode: 'raw',
      },
    )
    if (!active || sequence !== analysisSequence || identity !== JSON.stringify(base())) return
    const request = response.data.agent_request
    if (!request) throw new Error('No persisted request')
    selectedRequest.value = request as unknown as Record<string, unknown>
    requests.value = [selectedRequest.value, ...requests.value]
    analysis.value = { state: 'awaiting_agent' } as SchemaFinanceContentOut
    await router.replace({ query: { ...route.query, analysis: String(request.id) } })
  } catch {
    if (active && sequence === analysisSequence)
      error.value =
        'Analysis request could not be verified. Inspect Agent requests before retrying.'
  } finally {
    if (active && sequence === analysisSequence) analysisLoading.value = false
  }
}
async function refreshAnalysis() {
  if (!selectedRequest.value || !access.value || !reportAllowed.value) return
  const sequence = ++analysisSequence,
    identity = JSON.stringify(base())
  analysisLoading.value = true
  analysis.value = null
  error.value = ''
  const id = selectedRequest.value.id
  try {
    const request = await callOperation<Record<string, unknown>>('agentRequest.get', {
      project_id: projectId.value,
      request_id: id,
      response_mode: 'raw',
    })
    if (!active || sequence !== analysisSequence || identity !== JSON.stringify(base())) return
    selectedRequest.value = request
    const originalQuery = object(object(object(request.metadata_json).metadata_json).query)
    const value = await callOperation<SchemaFinanceContentOut>('finance.report.render', {
      ...originalQuery,
      ...base(),
      format: 'markdown',
      report_kind: 'analysis',
      analysis_request_id: id,
      response_mode: 'raw',
    })
    if (active && sequence === analysisSequence && identity === JSON.stringify(base()))
      analysis.value = value
  } catch {
    if (active && sequence === analysisSequence)
      error.value =
        'Analysis result unavailable, mismatched or unsupported. Inspect its original request and current authorization.'
  } finally {
    if (active && sequence === analysisSequence) analysisLoading.value = false
  }
}
onBeforeUnmount(() => {
  active = false
  clearScope()
})
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Finance"
      description="Sourced expenses, income and reports from your selected external master."
    >
      <template #actions
        ><UiButton data-test="refresh-finance" @click="refresh">Refresh</UiButton
        ><UiButton @click="configuring = !configuring">Workspace access</UiButton></template
      >
    </ProjectPageHeader>
    <UiCallout v-if="error" variant="danger" role="alert">{{ error }}</UiCallout>
    <p v-if="message" role="status" class="text-sm text-fg-muted">{{ message }}</p>
    <form
      v-if="configuring || !access"
      class="space-y-3 rounded-md border border-default p-4"
      @submit.prevent="authorizeWorkspace"
    >
      <h2 class="t-h2">Select an existing finance workspace</h2>
      <p class="text-sm text-fg-muted">
        Authorize this project to use an existing folder containing finance.json. Selection does not
        initialize or replace financial records.
      </p>
      <div class="grid grid-cols-2 gap-3">
        <UiFormField label="External finance folder" required
          ><UiInput v-model="root" placeholder="Existing local folder" /></UiFormField
        ><UiFormField label="Workspace reference" required
          ><UiInput v-model="workspace"
        /></UiFormField>
      </div>
      <div class="flex gap-4">
        <UiCheckbox v-model="allowCorrection" label="Allow sourced corrections" /><UiCheckbox
          v-model="allowExport"
          label="Allow private exports"
        />
      </div>
      <UiButton type="submit" variant="primary">Authorize existing workspace</UiButton>
      <UiButton v-if="access" variant="danger" @click="revoke">Revoke current access</UiButton>
    </form>
    <template v-if="access">
      <UiFormField v-if="accessRows.length > 1" label="Selected workspace"
        ><UiSelect
          :model-value="access.workspace_ref"
          :options="
            accessRows.map((row) => ({
              value: text(row.workspace_ref),
              label: text(row.workspace_ref),
              disabled: row.readiness !== 'ready',
            }))
          "
          @update:model-value="selectWorkspace"
      /></UiFormField>
      <p class="text-xs text-fg-muted">
        {{ access.workspace_ref }} · access generation {{ access.generation }} ·
        {{ access.modes.join(', ') }}
      </p>
      <FinanceFilters :query="query" :disabled="loading" @apply="loadQuery" />
      <div class="mt-4 grid min-w-0 grid-cols-[180px_minmax(0,1fr)] gap-5">
        <SubNav
          :groups="[
            {
              items: [
                { key: 'overview', label: 'Overview' },
                { key: 'records', label: 'Expenses & income' },
                { key: 'reports', label: 'Reports' },
              ],
            },
          ]"
          :active-key="section"
          aria-label="Finance views"
          @change="section = $event"
        />
        <main class="min-w-0 space-y-5" aria-label="Finance selected view">
          <p v-if="loading" role="status">Loading selected source…</p>
          <template v-if="projection">
            <p class="text-sm text-fg-muted">
              {{ query.start }} through {{ query.end }} · {{ query.recognition_basis }} recognition
              · {{ query.date_basis }} dates · {{ query.timezone }} · prepared/unposted · coverage
              {{ text(projection.coverage.state) }}
            </p>
            <details class="text-xs text-fg-muted">
              <summary class="focus-ring cursor-pointer">Source and reporting gaps</summary>
              <p class="break-all">
                Revision {{ projection.snapshot.revision }} · SHA-256
                {{ projection.snapshot.sha256 }} · {{ projection.snapshot.read_at }}
              </p>
              <p v-for="warning in projection.warnings" :key="warning">{{ warning }}</p>
              <p v-for="gap in refs(projection.coverage.issues)" :key="gap">
                {{ gap.replaceAll('_', ' ') }}
              </p>
              <p v-for="(item, index) in projection.exclusions" :key="index">
                {{ text(item.record_ref) }} · {{ text(item.reason).replaceAll('_', ' ') }}
              </p>
            </details>
            <section v-if="projection.undated_or_period_only.length" class="text-sm">
              <h3 class="t-h3">Undated or period-only records</h3>
              <p class="text-fg-muted">
                These remain inspectable and are excluded from dated metrics.
              </p>
              <UiButton
                v-for="refValue in projection.undated_or_period_only"
                :key="refValue"
                size="sm"
                variant="ghost"
                @click="inspect(refValue)"
                >{{ refValue }}</UiButton
              >
            </section>
            <template v-if="section === 'overview'">
              <p v-if="!projection.metrics_by_currency.length" class="text-sm text-fg-muted">
                Metrics unavailable. Records remain inspectable; confirm reporting policy, treatment
                and source coverage.
              </p>
              <section
                v-for="metric in projection.metrics_by_currency"
                :key="text(metric.currency)"
                class="space-y-3"
              >
                <h2 class="t-h2">{{ text(metric.currency) }} · supported operating result</h2>
                <div class="grid grid-cols-3 gap-3">
                  <div v-for="name in ['spend', 'revenue', 'profit']" :key="name">
                    <UiMetricCard
                      :label="
                        name === 'spend'
                          ? 'Spend'
                          : name === 'revenue'
                            ? 'Revenue'
                            : 'Operating profit'
                      "
                      :value="metricText(metric[name])"
                      :hint="text(object(metric[name]).state) || 'Unavailable'"
                    /><UiButton
                      v-if="refs(object(metric[name]).included_record_refs).length"
                      size="sm"
                      variant="ghost"
                      @click="includedRefs = refs(object(metric[name]).included_record_refs)"
                      >Inspect included records</UiButton
                    >
                    <p v-if="projection.comparison" class="mt-1 text-xs text-fg-muted">
                      Prior:
                      {{
                        metricText(
                          objects(projection.comparison.metrics_by_currency).find(
                            (m) => m.currency === metric.currency,
                          )?.[name],
                        )
                      }}
                    </p>
                  </div>
                </div>
              </section>
              <section v-for="group in groups" :key="group.dimension" class="space-y-2">
                <h3 class="t-h3">By {{ group.dimension }}</h3>
                <DataTable
                  :items="amountRows(group.rows)"
                  :columns="[
                    { key: 'label', label: 'Group' },
                    { key: 'currency', label: 'Currency' },
                    { key: 'spend', label: 'Spend' },
                    { key: 'revenue', label: 'Revenue' },
                  ]"
                  interactive
                  :aria-label="`${group.dimension} breakdown`"
                  max-height="220px"
                  @row-click="drill(group.dimension, $event)"
                />
              </section>
              <section>
                <h3 class="t-h3">Trend by date</h3>
                <DataTable
                  :items="amountRows(projection.trend)"
                  :columns="[
                    { key: 'date', label: 'Date' },
                    { key: 'currency', label: 'Currency' },
                    { key: 'spend', label: 'Spend' },
                    { key: 'revenue', label: 'Revenue' },
                  ]"
                  aria-label="Finance trend"
                  max-height="220px"
                />
              </section>
            </template>
            <section v-if="includedRefs.length" class="space-y-2">
              <h3 class="t-h3">Included records</h3>
              <UiButton
                v-for="refValue in includedRefs"
                :key="refValue"
                size="sm"
                variant="ghost"
                @click="
                  inspect(
                    refValue,
                    projection.rows.find((row) => row.record_ref === refValue),
                  )
                "
                >{{ refValue }}</UiButton
              >
            </section>
            <section v-if="section === 'records'" class="space-y-3">
              <UiSectionHeader
                title="Expenses & income"
                :description="`${projection.page.total} records in this selection; bills and receipts may await classification.`"
                ><template #actions
                  ><UiButton :disabled="!access.modes.includes('correct')" @click="createEntry"
                    >New expense or income</UiButton
                  ></template
                ></UiSectionHeader
              ><DataTable
                :items="rows"
                :columns="columns"
                interactive
                aria-label="Finance records"
                max-height="440px"
                :selected-id="recordRef"
                :sort-key="query.sort"
                :sort-dir="query.descending ? 'desc' : 'asc'"
                @row-click="inspect(text($event.record_ref), $event)"
                @sort="
                  (key, dir) =>
                    loadQuery({
                      ...query,
                      sort: (key === 'amount' ? 'amount' : 'date') as FinanceQuery['sort'],
                      descending: dir === 'desc',
                      offset: 0,
                    })
                "
              />
              <div class="flex gap-2">
                <UiButton
                  :disabled="!query.offset"
                  @click="
                    loadQuery({
                      ...query,
                      offset: Math.max(0, (query.offset || 0) - (query.limit || 50)),
                    })
                  "
                  >Previous page</UiButton
                ><UiButton
                  :disabled="!projection.page.has_more"
                  @click="
                    loadQuery({ ...query, offset: (query.offset || 0) + (query.limit || 50) })
                  "
                  >Next page</UiButton
                >
              </div>
            </section>
            <section v-if="section === 'reports'" class="space-y-3">
              <UiSectionHeader
                title="Reports"
                description="The same selected timeframe, filters and complete server population."
              />
              <div class="flex gap-2">
                <UiButton
                  :disabled="!reportAllowed || reportLoading"
                  @click="loadReport('markdown')"
                  >View period report</UiButton
                ><UiButton :disabled="!reportAllowed || reportLoading" @click="loadReport('csv')"
                  >Prepare CSV export</UiButton
                ><UiButton :disabled="!reportAllowed || reportLoading" @click="loadReport('json')"
                  >Prepare JSON export</UiButton
                >
              </div>
              <p v-if="reportLoading" role="status">Preparing private report…</p>
              <a
                v-if="reportURL"
                :href="reportURL"
                :download="report?.filename || 'finance-report'"
                class="focus-ring text-fg-link"
                >Download {{ report?.filename || 'private report' }}</a
              >
              <p v-if="report?.snapshot" class="break-all text-xs text-fg-muted">
                Source revision {{ report.snapshot.revision }} · {{ report.snapshot.sha256 }}
              </p>
              <MarkdownView
                v-if="reportText && report?.content_type?.includes('markdown')"
                :source="reportText"
              />
            </section>
            <section
              class="space-y-3 border-t border-subtle pt-4"
              aria-label="Agent finance analysis"
            >
              <UiSectionHeader
                title="Ask for analysis"
                description="An available host reads the selected source, analyzes it and obtains independent control review. Queueing does not execute a model."
              />
              <div class="flex items-end gap-3">
                <UiFormField label="Analysis question"
                  ><UiSelect
                    v-model="analysisKind"
                    :options="[
                      { value: 'opportunities', label: 'Supported opportunities' },
                      { value: 'spending', label: 'Spending review' },
                      { value: 'collections', label: 'Collection timing' },
                      { value: 'cashflow', label: 'Cashflow questions' },
                      { value: 'review-records', label: 'Source quality' },
                    ]" /></UiFormField
                ><UiButton
                  :disabled="!reportAllowed || loading || analysisLoading"
                  @click="requestAnalysis"
                  >Request agent analysis</UiButton
                >
              </div>
              <p role="status" class="text-sm">{{ statusText }}</p>
              <UiFormField v-if="requests.length" label="Previous analysis requests"
                ><UiSelect
                  :model-value="String(selectedRequest?.id || '')"
                  :options="
                    requests.map((r) => ({
                      value: String(r.id),
                      label: `Request ${r.id} · ${text(r.status)}`,
                    }))
                  "
                  @update:model-value="
                    (value) => {
                      selectedRequest = requests.find((r) => String(r.id) === String(value)) || null
                      void refreshAnalysis()
                    }
                  " /></UiFormField
              ><template v-if="selectedRequest"
                ><p class="text-xs text-fg-muted">
                  Request {{ selectedRequest.id }} · source
                  {{
                    text(
                      object(
                        object(object(selectedRequest.metadata_json).metadata_json).source_snapshot,
                      ).sha256,
                    )
                  }}
                </p>
                <RouterLink
                  :to="`/projects/${projectId}/agent-requests?request=${selectedRequest.id}`"
                  class="focus-ring text-fg-link"
                  >Inspect agent request and linked run</RouterLink
                ><UiButton
                  :loading="analysisLoading"
                  :disabled="!reportAllowed"
                  @click="refreshAnalysis"
                  >Refresh analysis status</UiButton
                >
                <p class="text-xs text-fg-muted">
                  Original window:
                  {{
                    text(
                      object(object(object(selectedRequest.metadata_json).metadata_json).query)
                        .start,
                    )
                  }}
                  through
                  {{
                    text(
                      object(object(object(selectedRequest.metadata_json).metadata_json).query).end,
                    )
                  }}. Results retain the original request window and source. Selecting a finding
                  does not adopt a financial decision.
                </p></template
              ><UiCallout v-if="analysis?.state === 'stale'" variant="warning"
                >Historical result: source changed after review. Request new analysis for current
                decisions.</UiCallout
              ><MarkdownView
                v-if="analysis?.report && ['current', 'stale'].includes(analysis.state)"
                :source="analysis.report"
              />
              <ul v-if="analysis?.result" class="space-y-3">
                <li
                  v-for="finding in objects(analysis.result.opportunities)"
                  :key="text(finding.opportunity_ref)"
                >
                  <p class="font-medium">{{ text(finding.kind).replaceAll('-', ' ') }}</p>
                  <p>{{ text(finding.rationale) }}</p>
                  <p class="text-sm text-fg-muted">Uncertainty: {{ text(finding.uncertainty) }}</p>
                  <p v-if="finding.estimated_impact">
                    Estimated impact: {{ formatMoney(finding.estimated_impact) }}
                  </p>
                  <UiButton
                    v-for="evidence in refs(finding.evidence_refs)"
                    :key="evidence"
                    size="sm"
                    variant="ghost"
                    @click="inspect(evidence)"
                    >{{ evidence }}</UiButton
                  >
                </li>
              </ul>
            </section>
          </template>
        </main>
      </div>
      <FinanceRecordPanel
        v-if="projection"
        v-model="panelOpen"
        :project-id="projectId"
        :access="access"
        :snapshot="projection.snapshot"
        :record-ref="recordRef"
        :reporting="object(selectedRow.reporting)"
        :exponent="
          typeof object(selectedRow.amount).exponent === 'number'
            ? (object(selectedRow.amount).exponent as number)
            : null
        "
        @refresh="refresh"
        @saved="recordSaved"
      />
    </template>
  </UiPageShell>
</template>
