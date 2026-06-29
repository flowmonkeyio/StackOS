<script setup lang="ts">
// CostBudgetTab — read-only current-month cost and budget visibility.

import { computed, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute } from 'vue-router'

import DataTable from '@/components/DataTable.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import {
  UiButton,
  UiCallout,
  UiCard,
  UiEmptyState,
  UiMetricCard,
  UiProgressBar,
  UiSectionHeader,
  UiSparkline,
} from '@/components/ui'
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

const route = useRoute()
const costsStore = useCostsStore()

const projectId = computed<number>(() => Number.parseInt(route.params.id as string, 10))
const { cost, budgets, history, hasNoSpendYet, month, loading } = storeToRefs(costsStore)

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

async function load(): Promise<void> {
  if (!projectId.value || Number.isNaN(projectId.value)) return
  await Promise.all([
    costsStore.refreshCost(projectId.value),
    costsStore.refreshBudgets(projectId.value),
    costsStore.refreshHistory(projectId.value, 12),
  ])
}

onMounted(load)
</script>

<template>
  <section class="space-y-5">
    <div class="flex justify-end">
      <UiButton
        size="sm"
        variant="secondary"
        icon-left="refresh"
        :loading="loading"
        @click="load"
      >
        Refresh
      </UiButton>
    </div>

    <div class="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      <UiMetricCard
        label="Current month"
        :value="formatUsd(cost?.total_usd)"
        :delta="month ?? undefined"
        delta-label="period"
        delta-tone="neutral"
        density="compact"
        :loading="loading && !cost"
      />
      <UiMetricCard
        label="Budget cap"
        :value="formatUsd(totalBudgetCap)"
        :delta="formatPercent(budgetUsagePercent)"
        delta-label="used"
        :delta-tone="
          budgetUsagePercent >= 100 ? 'negative' : budgetUsagePercent >= 80 ? 'neutral' : 'positive'
        "
        density="compact"
      />
      <UiMetricCard
        label="Tracked calls"
        :value="totalBudgetCalls"
        delta-label="this month"
        delta-tone="neutral"
        density="compact"
      />
      <UiMetricCard
        label="Configured caps"
        :value="budgets.length"
        :delta="`${INTEGRATION_KINDS.length} vendors`"
        delta-label="available"
        delta-tone="neutral"
        density="compact"
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
        :loading="loading"
        aria-label="Cost breakdown by integration"
        empty-message="No integration cost data yet."
      />
    </section>

    <section aria-label="Budget caps">
      <UiSectionHeader
        title="Budget caps"
        description="Per-vendor monthly caps, alert thresholds, and request pacing."
        as="h3"
      />

      <UiEmptyState
        v-if="!loading && budgets.length === 0"
        title="No budget caps"
        description="Agent-owned caps will appear here with alert thresholds and pacing."
        icon="banknotes"
        framed
      />

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
        v-if="sparklinePoints.length === 0 || !historyHasSpend"
        title="No cost history yet"
        description="The trend appears once a monthly snapshot records vendor spend."
        icon="banknotes"
        framed
      />
      <UiCard v-else>
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
            {{ point.label }}: {{ point.display }}
          </li>
        </ul>
      </UiCard>
    </section>
  </section>
</template>
