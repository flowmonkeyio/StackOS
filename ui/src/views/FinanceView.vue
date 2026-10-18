<script lang="ts">
import { reactive } from 'vue'

type AnalysisSubmission = { payloadJson: string; inFlight: boolean }
// Keep only the safe queue payload in memory across project route remounts.
// An unknown submission must not become a new intent when the selection changes.
const analysisSubmissions = reactive(new Map<number, AnalysisSubmission>())
</script>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type {
  SchemaFinanceAccessOut,
  SchemaFinanceContentOut,
  SchemaFinanceProjectionOut,
  SchemaFinanceStatusOut,
  SchemaLocalAgentChatMessageOut,
} from '@/api'
import DataTable from '@/components/DataTable.vue'
import MarkdownView from '@/components/MarkdownView.vue'
import SubNav from '@/components/SubNav.vue'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import FinanceFilters from '@/components/finance/FinanceFilters.vue'
import FinanceRecordPanel from '@/components/finance/FinanceRecordPanel.vue'
import FinanceOverview from './finance/FinanceOverview.vue'
import FinanceOverviewInsights from './finance/FinanceOverviewInsights.vue'
import { aiCategory, insightsModel } from './finance/insightsViewModel'
import {
  UiButton,
  UiCallout,
  UiFormField,
  UiPageShell,
  UiSectionHeader,
  UiSelect,
} from '@/components/ui'
import type { DataTableColumn } from '@/components/types'
import { useProjectRouteScope } from '@/composables/useProjectRouteScope'
import { useProjectScopedLoader } from '@/composables/useProjectScopedLoader'
import { callOperation } from '@/lib/operations'
import { ApiError } from '@/lib/client'
import {
  contentBlob,
  contentText,
  documentSummaryText,
  formatMoney,
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
const access = ref<SchemaFinanceAccessOut | null>(null)
const projection = ref<SchemaFinanceProjectionOut | null>(null)
const query = ref<FinanceQuery>(initialQuery())
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
    ...timeframe('year'),
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
type DisplayRow = SchemaFinanceProjectionOut['rows'][number] & {
  id: string
  invoice_status: string
  receipt_status: string
  ai_category: string
  ai_confidence: string
}
const insights = computed(() => insightsModel(analysis.value, projection.value))
const rows = computed<DisplayRow[]>(() =>
  (projection.value?.rows ?? []).map((row) => ({
    ...row,
    id: text(row.record_ref),
    invoice_status: documentSummaryText(object(row.document_summary).invoice),
    receipt_status: documentSummaryText(object(row.document_summary).receipt),
    ai_category: aiCategory(insights.value, text(row.record_ref)).category,
    ai_confidence: aiCategory(insights.value, text(row.record_ref)).confidence,
  })),
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
  { key: 'category_ref', label: 'Source category', format: (value) => text(value) || 'Unassigned' },
  { key: 'status', label: 'State' },
  { key: 'invoice_status', label: 'Invoice', cellClass: 'whitespace-nowrap' },
  { key: 'receipt_status', label: 'Receipt', cellClass: 'whitespace-nowrap' },
  { key: 'needs_review', label: 'Review', format: (value) => (value ? 'Needs review' : 'Sourced') },
  { key: 'ai_category', label: 'Proposed AI category' },
  { key: 'ai_confidence', label: 'AI confidence' },
]
const reportAllowed = computed(() => access.value?.modes.includes('export') === true)
const pendingAnalysis = computed(() => analysisSubmissions.get(projectId.value))
const pendingArguments = computed<Record<string, unknown> | null>(() =>
  pendingAnalysis.value ? JSON.parse(pendingAnalysis.value.payloadJson) : null,
)
const pendingMetadata = computed(() => object(pendingArguments.value?.metadata_json))
const retryAllowed = computed(
  () =>
    reportAllowed.value &&
    pendingMetadata.value.workspace_ref === access.value?.workspace_ref &&
    pendingMetadata.value.access_generation === access.value?.generation,
)
const statusText = computed(() => {
  if (pendingAnalysis.value)
    return pendingAnalysis.value.inFlight
      ? 'Submitting original analysis request…'
      : 'Analysis request submission is unverified'
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
        'run-blocked': 'Agent run blocked — inspect the linked run',
        'run-aborted': 'Agent run aborted — reviewed result not published',
        completed_unpublished: 'Run completed — reviewed result not published',
        run_unavailable: 'Linked run unavailable — inspect the agent request',
        responded: 'Awaiting reviewed publication',
        failed: 'Analysis failed',
        ignored: 'Request ignored',
        claim_expired: 'Agent claim expired',
        historical_access: 'Historical project Finance data',
        historical_source: 'Analysis request is historical — source changed',
        source_unavailable: 'Request source unavailable — request new analysis',
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
  projection.value = null
  requests.value = []
  selectedRequest.value = null
  analysis.value = null
  analysisLoading.value = false
  panelOpen.value = false
  selectedRow.value = {}
  includedRefs.value = []
  error.value = ''
  message.value = ''
  loading.value = false
}
function financeStatusMessage(code: string): string {
  if (code === 'storage_owner_mismatch')
    return "These Finance files belong to another project. Restore this project's matching files from a verified backup, then refresh. No existing records were replaced."
  if (code === 'atomic_publication_unavailable')
    return 'Project Finance data could not be safely initialized. Repair the local StackOS installation, then refresh. Existing files are preserved.'
  if (
    [
      'migration_required',
      'legacy_access_corrupt',
      'legacy_history_requires_recovery',
      'legacy_project_identity_ambiguous',
      'legacy_root_changed',
      'legacy_root_overlap',
      'legacy_root_shared',
      'legacy_mapping_changed',
      'legacy_source_changed',
      'migration_too_large',
    ].includes(code)
  )
    return 'Existing Finance data needs a one-time migration or repair before this project can open it. Ask an agent to inspect the existing source records and project ownership, then refresh. No existing records were replaced.'
  if (['not_configured', 'storage_missing', 'storage_metadata_missing'].includes(code))
    return 'Project Finance data is missing. Restore a verified backup if records already exist, then refresh. No existing records were replaced.'
  if (
    [
      'corrupt_workspace',
      'storage_metadata_corrupt',
      'publication_metadata_corrupt',
      'storage_custody_requires_repair',
      'storage_requires_repair',
      'storage_not_ready',
      'unreadable_or_invalid_workspace',
    ].includes(code)
  )
    return 'Project Finance data needs repair. Preserve the existing files and repair the records or restore a verified backup, then refresh. No existing records were replaced.'
  if (
    [
      'root_changed',
      'storage_identity_mismatch',
      'unsafe_storage_metadata',
      'unsafe_storage_alias',
      'unsafe_storage_lock',
      'unsafe_or_changing_storage',
      'storage_lock_changed',
      'storage_changed',
      'storage_publication_incomplete',
      'storage_publication_conflict',
      'storage_outcome_unknown',
    ].includes(code)
  )
    return 'Project Finance storage could not be safely verified. Check the existing records and project ownership, or restore a verified backup, then refresh. No existing records were replaced.'
  const messages: Record<string, string> = {
    unreadable: 'Project Finance data cannot be read. Check local file permissions, then refresh.',
    unsupported_version:
      'Project Finance data uses an unsupported version. Use a compatible StackOS version and preserve the existing files.',
    denied: 'Project Finance data access was denied. Check local permissions, then refresh.',
    access_denied: 'Project Finance data access was denied. Check local permissions, then refresh.',
    invalid_root:
      'Project Finance storage could not be safely read. Check local permissions and preserve the existing files, then refresh.',
    plugin_not_enabled:
      'Finance is not enabled for this project. Enable Finance in Plugins, then refresh.',
    writer_busy:
      'Project Finance data is busy. Wait for the current write or backup to finish, then refresh.',
  }
  return Object.prototype.hasOwnProperty.call(messages, code)
    ? messages[code]!
    : 'Project Finance data is unavailable. Refresh or check the local storage.'
}
async function loadStatus() {
  const sequence = ++scopeSequence,
    id = projectId.value
  querySequence++
  analysisSequence++
  analysis.value = null
  loading.value = true
  error.value = ''
  access.value = null
  projection.value = null
  panelOpen.value = false
  releaseReport()
  try {
    const response = await callOperation<{ data: SchemaFinanceStatusOut }>('finance.status', {
      project_id: id,
      response_mode: 'compact',
    })
    if (!active || sequence !== scopeSequence || id !== projectId.value) return
    if (
      response.data.project_id !== id ||
      response.data.access.length > 1 ||
      response.data.access.some((row) => row.project_id !== id)
    ) {
      error.value = 'Project Finance data could not be verified for this project. Refresh to retry.'
      return
    }
    const chosen = response.data.access[0]
    access.value = chosen?.readiness === 'ready' ? (chosen as SchemaFinanceAccessOut) : null
    if (!access.value) {
      const readiness = text(chosen?.readiness || response.data.readiness)
      error.value = financeStatusMessage(readiness)
      return
    }
    await loadQuery(query.value)
    if (active && sequence === scopeSequence) await loadRequests()
  } catch (cause) {
    if (active && sequence === scopeSequence) {
      access.value = null
      projection.value = null
      const code = cause instanceof ApiError ? text(object(cause.data).finance_code) : ''
      error.value = financeStatusMessage(code)
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
        'Selected finance data is unavailable. Check dates, basis, sources and project Finance storage.'
  } finally {
    if (active && sequence === querySequence) loading.value = false
  }
}
const { refresh } = useProjectScopedLoader({
  projectId,
  load: loadStatus,
  onScopeExit: () => {
    active = false
    clearScope()
  },
})
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
    const pending = analysisSubmissions.get(projectId.value)
    const payload = pending ? JSON.parse(pending.payloadJson) : null
    const verified = payload
      ? page.items.find(
          (row) =>
            row.request_key === `local-agent-chat:${payload.thread_key}:${payload.message_key}` &&
            canonicalJson(object(row.metadata_json).metadata_json) ===
              canonicalJson(payload.metadata_json),
        )
      : undefined
    if (verified) {
      analysisSubmissions.delete(projectId.value)
      message.value = `Original analysis request ${verified.id} verified in Agent requests. It retains its original project, window and source.`
    }
    requests.value = page.items.filter(
      (row) =>
        object(object(row.metadata_json).metadata_json).workspace_ref ===
        access.value?.workspace_ref,
    )
    const requestedId = text(route.query.analysis)
    const requested =
      (verified && requests.value.find((row) => row.id === verified.id)) ||
      requests.value.find((row) => String(row.id) === requestedId)
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
function canonicalJson(value: unknown): string {
  return JSON.stringify(value, (_key, child) =>
    child && typeof child === 'object' && !Array.isArray(child)
      ? Object.fromEntries(Object.entries(child).sort(([a], [b]) => a.localeCompare(b)))
      : child,
  )
}
async function requestAnalysis() {
  const id = projectId.value
  let submission = analysisSubmissions.get(id)
  if (submission?.inFlight) return
  if (submission) {
    if (!retryAllowed.value) {
      error.value =
        'The original submission is still unverified and its project data identity has changed. Refresh to reconcile it in Agent requests before retrying.'
      return
    }
  } else {
    if (!access.value || !projection.value || loading.value || !reportAllowed.value) return
    if (text(projection.value.query.q) !== '') {
      error.value =
        'Private search text cannot be included in an agent request. Clear search and apply filters before requesting analysis; your current search and timeframe remain selected.'
      return
    }
    const intent = newIntentId(),
      originalQuery = { ...projection.value.query }
    delete originalQuery.q
    submission = {
      inFlight: false,
      payloadJson: JSON.stringify({
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
          source_snapshot: snapshotRef(projection.value.snapshot),
          query: originalQuery,
          analysis_kind: analysisKind.value,
          record_refs: [],
          source_refs: [],
          intent_key: intent,
        },
        response_mode: 'raw',
      }),
    }
    analysisSubmissions.set(id, submission)
    submission = analysisSubmissions.get(id)!
  }
  analysisSequence++
  const identity = JSON.stringify(base()),
    payload = JSON.parse(submission.payloadJson) as Record<string, unknown>
  submission.inFlight = true
  analysisLoading.value = false
  error.value = ''
  message.value = ''
  analysis.value = null
  selectedRequest.value = null
  try {
    const response = await callOperation<{ data: SchemaLocalAgentChatMessageOut }>(
      'localAgentChat.createMessage',
      payload,
    )
    const request = response.data.agent_request
    if (!request) throw new Error('No persisted request')
    // Queue reconciliation retires this occurrence before another request can start.
    // Its late acknowledgement must not replace the newer request or its selection.
    if (analysisSubmissions.get(id) !== submission) return
    analysisSubmissions.delete(id)
    if (!active || id !== projectId.value) return
    if (identity !== JSON.stringify(base())) {
      message.value = `Original analysis request ${request.id} saved. Inspect Agent requests for its original project, window and source.`
      return
    }
    selectedRequest.value = request as unknown as Record<string, unknown>
    requests.value = [
      selectedRequest.value,
      ...requests.value.filter((row) => row.id !== request.id),
    ]
    const originalSnapshot = object(object(payload.metadata_json).source_snapshot)
    const historical =
      projection.value &&
      canonicalJson(snapshotRef(projection.value.snapshot)) !== canonicalJson(originalSnapshot)
    analysis.value = historical
      ? ({ state: 'historical_source' } as SchemaFinanceContentOut)
      : ['new', 'pending'].includes(request.status)
        ? ({ state: 'awaiting_agent' } as SchemaFinanceContentOut)
        : null
    await router.replace({ query: { ...route.query, analysis: String(request.id) } })
  } catch {
    if (active && id === projectId.value && analysisSubmissions.get(id) === submission)
      error.value =
        'Analysis request could not be verified. Retry the retained original request or inspect Agent requests; a new request is paused until this submission is verified.'
  } finally {
    submission.inFlight = false
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
    requests.value = requests.value.map((row) =>
      String(row.id) === String(request.id) ? request : row,
    )
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
        'Analysis result unavailable, mismatched or unsupported. Inspect its original request and current project Finance data.'
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
      description="Expenses, income and documents stored with this project."
    >
      <template #actions>
        <UiButton data-test="refresh-finance" @click="refresh">Refresh</UiButton>
      </template>
    </ProjectPageHeader>
    <UiCallout v-if="error" variant="danger" role="alert">{{ error }}</UiCallout>
    <p v-if="loading && !access" role="status" class="text-sm text-fg-muted">
      Loading project Finance data…
    </p>
    <p v-if="message" role="status" class="text-sm text-fg-muted">{{ message }}</p>
    <UiCallout v-if="pendingAnalysis" variant="warning" role="status">
      <p>
        {{ pendingAnalysis.inFlight ? 'Submitting' : 'Unverified submission for' }}
        this project ·
        {{ text(object(pendingMetadata.query).start) }} through
        {{ text(object(pendingMetadata.query).end) }} · {{ text(pendingMetadata.analysis_kind) }} ·
        source revision {{ object(pendingMetadata.source_snapshot).revision }}. Retry retains this
        original window, question and source; changes to the current selection apply only to a new
        request after verification.
      </p>
      <UiButton :disabled="pendingAnalysis.inFlight || !retryAllowed" @click="requestAnalysis"
        >Retry original analysis request</UiButton
      >
      <RouterLink :to="`/projects/${projectId}/agent-requests`" class="focus-ring text-fg-link"
        >Inspect Agent requests</RouterLink
      >
      <p v-if="!retryAllowed" class="text-sm">
        The original project data identity has changed. Refresh to reconcile its persisted request
        in Agent requests before retrying.
      </p>
    </UiCallout>
    <template v-if="access">
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
            <FinanceOverview
              v-if="section === 'overview'"
              :projection="projection"
              @inspect="includedRefs = $event"
              @drill="drill"
            />
            <FinanceOverviewInsights
              v-if="section === 'overview'"
              :model="insights"
              :status="statusText"
              :has-request="!!selectedRequest"
              :request-allowed="reportAllowed && !loading && !analysisLoading && !pendingAnalysis"
              :refresh-allowed="reportAllowed && !!selectedRequest"
              :loading="analysisLoading"
              @request="requestAnalysis"
              @refresh="refreshAnalysis"
              @inspect="inspect($event)"
            />
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
              >
              <p class="text-xs text-fg-muted">
                Invoice documents the purchase; receipt documents payment. File availability alone
                does not confirm payment. Open a record to identify its linked originals.
              </p>
              <DataTable
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
                title="AI category and concern review"
                description="Choose a review or inspect a previous request."
              />
              <details class="text-sm text-fg-muted">
                <summary class="focus-ring cursor-pointer">How reviews work</summary>
                <p class="mt-2">
                  An available finance agent proposes descriptive categories and highlights
                  supported concerns, then obtains independent control review. Queueing does not
                  execute a model.
                </p>
              </details>
              <div class="flex items-end gap-3">
                <UiFormField label="Analysis question"
                  ><UiSelect
                    v-model="analysisKind"
                    :options="[
                      { value: 'opportunities', label: 'Categories and concerns' },
                      { value: 'spending', label: 'Spending review' },
                      { value: 'collections', label: 'Collection timing' },
                      { value: 'cashflow', label: 'Cashflow questions' },
                      { value: 'review-records', label: 'Source quality' },
                    ]" /></UiFormField
                ><UiButton
                  :disabled="!reportAllowed || loading || analysisLoading || !!pendingAnalysis"
                  @click="requestAnalysis"
                  >{{
                    selectedRequest
                      ? 'Request new category and concern review'
                      : 'Request category and concern review'
                  }}</UiButton
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
              >
              <details
                v-if="
                  analysis?.result ||
                  (analysis?.report && ['current', 'stale'].includes(analysis.state))
                "
                class="space-y-3"
              >
                <summary class="focus-ring cursor-pointer text-sm font-medium">
                  Full analysis
                </summary>
                <div class="space-y-3 pt-2">
                  <MarkdownView
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
                      <p class="text-sm text-fg-muted">
                        Uncertainty: {{ text(finding.uncertainty) }}
                      </p>
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
                </div>
              </details>
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
