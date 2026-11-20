<script setup lang="ts">
import type { SchemaFinanceContentOut } from '@/api'
import MarkdownView from '@/components/MarkdownView.vue'
import { UiButton, UiCallout, UiFormField, UiSelect } from '@/components/ui'
import { formatMoney, object, objects, refs, text } from '@/lib/finance'
import FinanceOverviewInsights from './FinanceOverviewInsights.vue'
import type { InsightsModel } from './insightsViewModel'

defineProps<{
  analysisKind: string
  selectedRequest: Record<string, unknown> | null
  requests: Record<string, unknown>[]
  statusText: string
  insights: InsightsModel
  analysis: SchemaFinanceContentOut | null
  reportAllowed: boolean
  loading: boolean
  analysisLoading: boolean
  pendingAnalysis: boolean
}>()
const emit = defineEmits<{
  'update:analysisKind': [kind: string]
  request: []
  refresh: []
  inspectRequest: []
  selectRequest: [id: string]
  inspect: [recordRef: string]
}>()
</script>
<template>
  <details
    class="space-y-3 border-t border-subtle pt-4"
    aria-label="Agent finance analysis"
  >
    <summary class="focus-ring cursor-pointer text-sm font-medium">
      Review finances <span class="ml-2 font-normal text-fg-muted">{{ statusText }}</span>
    </summary>
    <p class="text-sm text-fg-muted">
      Ask your finance agent to review this selection and suggest categories or highlight concerns.
    </p>
    <div class="flex flex-wrap items-end gap-3">
      <UiFormField label="Analysis question">
        <UiSelect
          :model-value="analysisKind"
          :options="[
            { value: 'opportunities', label: 'Categories and concerns' },
            { value: 'spending', label: 'Spending review' },
            { value: 'collections', label: 'Collection timing' },
            { value: 'cashflow', label: 'Cashflow questions' },
            { value: 'review-records', label: 'Source quality' },
          ]"
          @update:model-value="emit('update:analysisKind', String($event))"
        />
      </UiFormField><UiButton
        :disabled="!reportAllowed || loading || analysisLoading || !!pendingAnalysis"
        @click="emit('request')"
      >
        {{
          selectedRequest
            ? 'Request new category and concern review'
            : 'Request category and concern review'
        }}
      </UiButton>
    </div>
    <UiFormField
      v-if="requests.length"
      label="Previous analysis requests"
    >
      <UiSelect
        :model-value="String(selectedRequest?.id || '')"
        :options="
          requests.map((r) => ({
            value: String(r.id),
            label: `Request ${r.id} · ${text(r.status)}`,
          }))
        "
        @update:model-value="
          (value) => {
            emit('selectRequest', String(value))
          }
        "
      />
    </UiFormField><template v-if="selectedRequest">
      <UiButton
        data-test="inspect-finance-request"
        :data-request-id="selectedRequest.id"
        @click="emit('inspectRequest')"
      >
        Inspect analysis request
      </UiButton><UiButton
        :loading="analysisLoading"
        :disabled="!reportAllowed"
        @click="emit('refresh')"
      >
        Refresh analysis status
      </UiButton>
      <p class="text-xs text-fg-muted">
        Original window:
        {{ text(object(object(object(selectedRequest.metadata_json).metadata_json).query).start) }}
        through
        {{ text(object(object(object(selectedRequest.metadata_json).metadata_json).query).end) }}.
        Results retain the original request window and source. Selecting a finding does not adopt a
        financial decision.
      </p>
    </template><UiCallout
      v-if="analysis?.state === 'stale'"
      variant="warning"
    >
      Historical result: source changed after review. Request new analysis for current
      decisions.
    </UiCallout>
    <FinanceOverviewInsights
      v-if="insights.hasResult"
      :show-controls="false"
      :model="insights"
      :status="statusText"
      :has-request="!!selectedRequest"
      :request-allowed="reportAllowed && !loading && !analysisLoading && !pendingAnalysis"
      :refresh-allowed="reportAllowed && !!selectedRequest"
      :loading="analysisLoading"
      @request="emit('request')"
      @refresh="emit('refresh')"
      @inspect="emit('inspect', $event)"
    />
    <details
      v-if="analysis?.result || (analysis?.report && ['current', 'stale'].includes(analysis.state))"
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
        <ul
          v-if="analysis?.result"
          class="space-y-3"
        >
          <li
            v-for="finding in objects(analysis.result.opportunities)"
            :key="text(finding.opportunity_ref)"
          >
            <p class="font-medium">
              {{ text(finding.kind).replaceAll('-', ' ') }}
            </p>
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
              @click="emit('inspect', evidence)"
            >
              {{ evidence }}
            </UiButton>
          </li>
        </ul>
      </div>
    </details>
  </details>
</template>
