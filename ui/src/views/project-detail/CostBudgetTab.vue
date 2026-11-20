<script setup lang="ts">
// CostBudgetTab — read-only current-month cost and budget visibility.

import { computed } from 'vue'
import { storeToRefs } from 'pinia'

import DataTable from '@/components/DataTable.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import {
  UiButton,
  UiCallout,
  UiCard,
  UiEmptyState,
  UiLoadingState,
  UiMetricCard,
  UiProgressBar,
  UiSectionHeader,
  UiSparkline,
} from '@/components/ui'
import { useProjectRouteScope } from '@/composables/useProjectRouteScope'
import { useProjectScopedLoader } from '@/composables/useProjectScopedLoader'
import { resolveStatus } from '@/design/status'
import { formatPercent, formatUsd } from '@/lib/stackos/format'
import {
  INTEGRATION_KINDS,
  useCostsStore,
  type IntegrationBudget,
  type IntegrationKind,
} from '@/stores/costs'
import type { DataTableColumn } from '@/components/types'
import type { SparklinePoint } from '@/components/ui/UiSparkline.vue'

const costsStore = useCostsStore()

const { projectId } = useProjectRouteScope()
const { cost, budgets, history, hasNoSpendYet, month, loading, error,
  costLoading, budgetsLoading, historyLoading, budgetsLoaded, costError, budgetsError, historyErrors, historyEmpty } = storeToRefs(costsStore)

type IntegrationKindLabels = { [Key in IntegrationKind]: string }

const integrationLabels: IntegrationKindLabels = {
  dataforseo: 'DataForSEO',
  firecrawl: 'Firecrawl',
  'openai-images': 'OpenAI Images',
  reddit: 'Reddit',
  'google-paa': 'Google PAA',
  jina: 'Jina Reader',
  ahrefs: 'Ahrefs',
}

const integrationCostRows = computed(() => {
  if (!cost.value) return []
  return Object.entries(cost.value.by_integration).map(([kind, spend]) => ({
    id: kind,
    integration: integrationLabel(kind),
    spend,
  }))
})

const integrationColumns: DataTableColumn<{
  id: string
  integration: string
  spend: number
}>[] = [
  { key: 'integration', label: 'Integration' },
  { key: 'spend', label: 'Spend (USD)', format: (value) => formatUsd(Number(value)) },
]

const totalBudgetCap = computed(() =>
  budgets.value.reduce((sum, budget) => sum + budget.monthly_budget_usd, 0),
)

const totalBudgetCalls = computed(() =>
  budgets.value.reduce((sum, budget) => sum + budget.current_month_calls, 0),
)

const budgetUsagePercent = computed(() =>
  totalBudgetCap.value > 0
    ? Math.min(100, (Number(cost.value?.total_usd ?? 0) / totalBudgetCap.value) * 100)
    : 0,
)

const sparklinePoints = computed<SparklinePoint[]>(() =>
  history.value.map((row) => {
    const ym = row.period_start.slice(0, 7)
    return {
      label: ym,
      value: row.total_usd,
      display: formatUsd(row.total_usd),
    }
  }),
)

const historyHasSpend = computed(() =>
  history.value.some((row) => Number(row.total_usd) > 0),
)
const failedMonths = computed(() => Object.keys(historyErrors.value))

function integrationLabel(kind: string): string {
  return INTEGRATION_KINDS.includes(kind as IntegrationKind)
    ? integrationLabels[kind as IntegrationKind]
    : kind
}

function budgetUsage(budget: IntegrationBudget): number {
  if (budget.monthly_budget_usd <= 0) return 0
  return Math.min(100, (budget.current_month_spend / budget.monthly_budget_usd) * 100)
}

// Map pacing to the canonical budget status domain (status.ts).
function budgetStatus(budget: IntegrationBudget): 'underBudget' | 'approaching' | 'overBudget' {
  const pct = budgetUsage(budget)
  if (pct >= 100) return 'overBudget'
  if (pct >= budget.alert_threshold_pct) return 'approaching'
  return 'underBudget'
}

function budgetTone(budget: IntegrationBudget): 'success' | 'warning' | 'danger' {
  return resolveStatus('budget', budgetStatus(budget)).tone as 'success' | 'warning' | 'danger'
}

async function load(nextProjectId: number): Promise<void> {
  await Promise.all([
    costsStore.refreshCost(nextProjectId),
    costsStore.refreshBudgets(nextProjectId),
    costsStore.refreshHistory(nextProjectId, 12),
  ])
}

const { refresh } = useProjectScopedLoader({
  projectId,
  load: ({ projectId }) => load(projectId),
  onScopeExit: () => costsStore.reset(),
})
</script>

<template>
  <section class="space-y-5">
    <div class="flex min-h-7 flex-wrap items-center justify-between gap-2">
      <div role="status" class="min-h-5 text-xs text-fg-muted">
        <details v-if="costError || budgetsError">
          <summary class="focus-ring cursor-pointer rounded-sm">Spend reads need refresh</summary>
          <UiCallout v-if="costError" tone="warning" title="Current month read failed" class="mt-2">
            {{ costError }} {{ cost ? 'Showing last accepted spend.' : 'Current spend is unavailable.' }}
          </UiCallout>
          <UiCallout v-if="budgetsError" tone="warning" title="Budget read failed" class="mt-2">
            {{ budgetsError }} {{ budgetsLoaded ? 'Showing last accepted budgets and calls.' : 'Budgets and calls are unavailable.' }}
          </UiCallout>
        </details>
        <p v-else>{{ loading ? cost || budgetsLoaded || history.length ? 'Refreshing Spend…' : 'Loading Spend…' : error ? 'Some Spend reads failed.' : 'Spend reads complete.' }}</p>
      </div>
      <UiButton
        size="sm"
        variant="secondary"
        icon-left="refresh"
        :loading="loading"
        @click="refresh"
      >
        Refresh
      </UiButton>
    </div>

    <div class="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      <UiMetricCard
        label="Current month"
        :value="cost ? formatUsd(cost.total_usd) : '—'"
        :delta="month ?? undefined"
        delta-label="period"
        delta-tone="neutral"
        density="compact"
        :loading="costLoading && !cost"
      />
      <UiMetricCard
        label="Budget cap"
        :value="budgetsLoaded ? formatUsd(totalBudgetCap) : '—'"
        :delta="cost && budgetsLoaded ? formatPercent(budgetUsagePercent) : undefined"
        delta-label="used"
        :delta-tone="
          budgetUsagePercent >= 100 ? 'negative' : budgetUsagePercent >= 80 ? 'neutral' : 'positive'
        "
        density="compact"
        :loading="budgetsLoading && !budgetsLoaded"
      />
      <UiMetricCard
        label="Tracked calls"
        :value="budgetsLoaded ? totalBudgetCalls : '—'"
        delta-label="this month"
        delta-tone="neutral"
        density="compact"
        :loading="budgetsLoading && !budgetsLoaded"
      />
      <UiMetricCard
        label="Configured caps"
        :value="budgetsLoaded ? budgets.length : '—'"
        :delta="`${INTEGRATION_KINDS.length} vendors`"
        delta-label="available"
        delta-tone="neutral"
        density="compact"
        :loading="budgetsLoading && !budgetsLoaded"
      />
    </div>

    <UiCallout
      v-if="hasNoSpendYet"
      tone="info"
      title="No spend recorded yet"
    >
      Cost rows will appear after integrations make tracked vendor calls.
    </UiCallout>

    <section aria-label="Current month spend">
      <UiSectionHeader
        title="Current month spend"
        :description="`Vendor spend recorded for ${month ?? 'the selected period'}.`"
        as="h3"
      />
      <DataTable
        :items="integrationCostRows"
        :columns="integrationColumns"
        :loading="costLoading && !cost"
        aria-label="Cost breakdown by integration"
        :empty-message="costError && !cost ? 'Current spend is unavailable.' : 'No integration cost data yet.'"
      />
    </section>

    <section aria-label="Budget caps">
      <UiSectionHeader
        title="Budget caps"
        description="Per-vendor monthly caps, alert thresholds, and request pacing."
        as="h3"
      />

      <UiEmptyState
        v-if="budgetsLoaded && budgets.length === 0"
        title="No budget caps"
        description="Agent-owned caps will appear here with alert thresholds and pacing."
        icon="banknotes"
        framed
      />
      <UiLoadingState v-else-if="budgetsLoading && !budgetsLoaded" label="Loading budget caps" />

      <div
        v-else
        class="grid gap-4"
      >
        <UiCard
          v-for="budget in budgets"
          :key="budget.id"
        >
          <template #header>
            <h4 class="t-h3 text-fg-strong">
              {{ integrationLabel(budget.kind) }}
            </h4>
            <StatusBadge
              domain="budget"
              :status="budgetStatus(budget)"
            />
          </template>

          <p class="text-sm text-fg-muted">
            {{ formatUsd(budget.current_month_spend) }} spent from a
            {{ formatUsd(budget.monthly_budget_usd) }} monthly cap.
          </p>

          <UiProgressBar
            class="mt-3"
            :value="budget.current_month_spend"
            :max="budget.monthly_budget_usd || 1"
            :tone="budgetTone(budget)"
            show-label
            :format="() => formatPercent(budgetUsage(budget))"
            :aria-label="`${integrationLabel(budget.kind)} budget usage`"
          />

          <template #footer>
            <dl class="grid w-full gap-3 text-sm sm:grid-cols-2 xl:grid-cols-4">
              <div>
                <dt class="text-xs font-medium text-fg-muted">
                  Alert
                </dt>
                <dd class="mt-0.5 tabular-nums text-fg-default">
                  {{ formatPercent(budget.alert_threshold_pct) }}
                </dd>
              </div>
              <div>
                <dt class="text-xs font-medium text-fg-muted">
                  Calls
                </dt>
                <dd class="mt-0.5 tabular-nums text-fg-default">
                  {{ budget.current_month_calls }}
                </dd>
              </div>
              <div>
                <dt class="text-xs font-medium text-fg-muted">
                  QPS
                </dt>
                <dd class="mt-0.5 tabular-nums text-fg-default">
                  {{ budget.qps }}
                </dd>
              </div>
              <div>
                <dt class="text-xs font-medium text-fg-muted">
                  Run-plan guard
                </dt>
                <dd class="mt-0.5 text-fg-muted">
                  Checked before vendor calls.
                </dd>
              </div>
            </dl>
          </template>
        </UiCard>
      </div>
    </section>

    <section aria-label="12-month history">
      <UiSectionHeader
        title="12-month history"
        description="Quick trend view for vendor spend over time."
        as="h3"
      />

      <UiEmptyState
        v-if="historyEmpty"
        title="No cost history yet"
        description="The trend appears once a monthly snapshot records vendor spend."
        icon="banknotes"
        framed
      />
      <UiLoadingState v-else-if="historyLoading && !history.length" label="Loading cost history" />
      <div class="min-h-5 text-xs text-fg-muted" role="status">
        <details v-if="failedMonths.length">
          <summary class="focus-ring cursor-pointer rounded-sm">History is incomplete · {{ failedMonths.length }} months failed</summary>
          <UiCallout tone="warning" class="my-2">
            <p>{{ historyEmpty ? 'Showing last accepted empty history. Refresh is incomplete.' : history.length ? 'Available months are shown below. Failed months with retained values are marked stale.' : 'Cost history is unavailable.' }}</p>
            <ul class="mt-2 space-y-1">
              <li v-for="ym in failedMonths" :key="ym">{{ ym }}: {{ historyErrors[ym] }}</li>
            </ul>
          </UiCallout>
        </details>
        <p v-else>{{ historyLoading ? 'Reading cost history…' : 'Cost history current.' }}</p>
      </div>
      <UiCard v-if="!historyEmpty && history.length && (historyHasSpend || failedMonths.length)">
        <UiSparkline
          :points="sparklinePoints"
          aria-label="Cost sparkline (last 12 months)"
          tone="accent"
        />
        <ul class="mt-2 grid grid-cols-4 gap-1 text-xs text-fg-muted lg:grid-cols-6">
          <li
            v-for="point in sparklinePoints"
            :key="point.label"
          >
            {{ point.label }}: {{ point.display }}{{ historyErrors[point.label] ? ' (stale)' : '' }}
          </li>
        </ul>
      </UiCard>
    </section>
  </section>
</template>
