<script setup lang="ts">
import { computed, ref } from 'vue'
import DataTable from '@/components/DataTable.vue'
import type { DataTableColumn } from '@/components/types'
import AgentUsageSummary from '@/components/domain/AgentUsageSummary.vue'
import { UiButton, UiButtonGroup, UiCallout, UiFormField, UiInput, UiSkeleton } from '@/components/ui'
import { formatAbsoluteDateTime } from '@/lib/stackos/time'
import AgentActivityChart from './AgentActivityChart.vue'
import { cacheShare, exactUsage, fieldCoverage, formatTokens, reviewFacts, type AgentStats, type AgentStatsFilters, type ActivityGroup, type ActivityMetric, type ActivityColor } from '@/lib/agentActivity'

const props = defineProps<{ summary: AgentStats | null; loading: boolean; error: string | null; filters: AgentStatsFilters; global?: boolean }>()
const emit = defineEmits<{ filters: [value: Partial<AgentStatsFilters>] }>()
const detailsOpen = ref(false)
const chartMetric = ref<ActivityMetric>('input')
const chartColor = ref<ActivityColor>('input_type')
const groups = [{ value: 'agent', label: 'Agent' }, { value: 'model', label: 'Model' }, { value: 'agent_model', label: 'Agent + model' }] as const
const rows = computed(() => props.summary?.rows.map(row => ({ ...row, id: row.group_key, input: row.reported_usage.input, cache_read: row.reported_usage.cache_read, output: row.reported_usage.output })) ?? [])
type Row = (typeof rows.value)[number]
const columns = computed<DataTableColumn<Row>[]>(() => [
  { key: 'agent_name', label: props.filters.group_by === 'model' ? 'Selected model' : props.filters.group_by === 'agent' ? 'Agent' : 'Agent + selected model' },
  { key: 'launch_attempts', label: 'Attempts', cellClass: 'tabular-nums' },
  { key: 'input', label: 'Input', cellClass: 'tabular-nums' },
  { key: 'cache_read', label: 'Cache', cellClass: 'tabular-nums' },
  { key: 'output', label: 'Output', cellClass: 'tabular-nums' },
])
const share = computed(() => props.summary ? cacheShare(props.summary.totals) : null)
function setDate(field: 'start_date' | 'end_date', value: string | number | null): void { emit('filters', { [field]: String(value ?? '') }) }
</script>

<template>
  <section
    aria-label="Agent activity"
    :aria-busy="loading"
    class="agent-activity"
  >
    <div class="agent-activity__header">
      <div>
        <h2 class="text-2xl font-semibold text-fg-strong">
          Agent usage
        </h2><p class="mt-1 text-xs text-fg-muted">
          Recorded StackOS automation · {{ filters.timezone }}
        </p>
      </div>
      <div
        class="agent-activity__dates"
        role="group"
        aria-label="Agent usage date range"
      >
        <UiFormField label="From">
          <UiInput
            type="date"
            :model-value="filters.start_date"
            @change="setDate('start_date', $event)"
          />
        </UiFormField>
        <UiFormField label="Through">
          <UiInput
            type="date"
            :model-value="filters.end_date"
            @change="setDate('end_date', $event)"
          />
        </UiFormField>
      </div>
    </div>
    <UiCallout
      v-if="error"
      tone="warning"
    >
      {{ error }}<span v-if="summary"> Previously loaded Agent activity is shown for this selection.</span>
    </UiCallout>
    <UiSkeleton
      v-if="loading && !summary"
      shape="block"
      height="8rem"
      aria-label="Loading Agent activity"
    />
    <template v-else-if="summary">
      <div class="agent-activity__headline">
        <AgentUsageSummary :metrics="summary.totals" />
        <div class="agent-activity__attempts">
          <p><strong>{{ summary.totals.launch_attempts.toLocaleString('en-US') }}</strong> agent {{ summary.totals.launch_attempts === 1 ? 'attempt' : 'attempts' }}</p>
          <p
            v-if="summary.totals.coverage.usage_unavailable_attempts"
            class="text-xs text-fg-muted"
          >
            Usage missing for {{ summary.totals.coverage.usage_unavailable_attempts }} {{ summary.totals.coverage.usage_unavailable_attempts === 1 ? 'attempt' : 'attempts' }}
          </p>
          <p class="mt-1 text-xs text-fg-muted">
            Cache is included in Input.
          </p>
        </div>
      </div>
      <p
        v-if="summary.undated.launch_attempts"
        class="text-xs text-fg-muted"
      >
        {{ summary.undated.launch_attempts }} {{ summary.undated.launch_attempts === 1 ? 'attempt has' : 'attempts have' }} no recorded start date and {{ summary.undated.launch_attempts === 1 ? 'is' : 'are' }} excluded from this range.
      </p>
      <p
        v-if="summary.totals.launch_attempts === 0"
        class="rounded-lg border border-default bg-bg-surface p-5 text-sm text-fg-muted"
      >
        No recorded usage in this date range.
      </p>
      <template v-else>
        <div class="agent-activity__analysis">
          <AgentActivityChart
            v-model:metric="chartMetric"
            v-model:color="chartColor"
            :days="summary.daily"
            :timezone="filters.timezone"
            :global="global"
          />
          <aside
            class="agent-activity__cache"
            aria-label="Input details"
          >
            <h3 class="text-lg font-semibold text-fg-strong">
              Where input goes
            </h3>
            <p
              v-if="share"
              class="agent-activity__share"
            >
              {{ share }}
            </p>
            <p
              v-else
              class="mt-4 text-sm text-fg-muted"
            >
              {{ summary.totals.reported_usage.input === 0 ? 'No input tokens recorded' : 'Cache share not available' }}
            </p>
            <p class="mt-1 text-xs text-fg-muted">
              {{ share ? 'Input reused from cache.' : summary.totals.reported_usage.input === 0 ? 'Measured zero is retained.' : 'Some cache measurements were not reported.' }}
            </p>
            <dl class="agent-activity__split">
              <div>
                <dt>Cache read</dt><dd :title="exactUsage(summary.totals, 'cache_read')">
                  {{ formatTokens(summary.totals.reported_usage.cache_read) }}
                </dd>
              </div>
              <div>
                <dt>Remaining input</dt><dd :title="formatTokens(summary.totals.reported_usage.remaining_input, false)">
                  {{ formatTokens(summary.totals.reported_usage.remaining_input) }}
                </dd>
              </div>
              <div v-if="summary.totals.reported_usage.input_with_unknown_cache">
                <dt>Cache split not reported</dt><dd :title="formatTokens(summary.totals.reported_usage.input_with_unknown_cache, false)">
                  {{ formatTokens(summary.totals.reported_usage.input_with_unknown_cache) }}
                </dd>
              </div>
            </dl>
            <p class="text-xs leading-5 text-fg-muted">
              Cache is included in Input. Remaining input includes cache write. Output is counted separately.
            </p>
            <UiButton
              class="agent-activity__details-toggle mt-3"
              variant="ghost"
              size="sm"
              icon-right="arrow-right"
              :aria-expanded="detailsOpen"
              @click="detailsOpen = !detailsOpen"
            >
              {{ detailsOpen ? 'Hide token details' : 'See token details' }}
            </UiButton>
            <div
              v-if="detailsOpen"
              class="agent-activity__details"
              aria-label="Token details"
            >
              <p><strong>Cache write:</strong> {{ formatTokens(summary.totals.reported_usage.cache_write, false) }} · included in Input. {{ fieldCoverage(summary.totals, 'cache_write') }}</p>
              <p><strong>Reasoning:</strong> {{ formatTokens(summary.totals.reported_usage.reasoning, false) }} · included in Output. {{ fieldCoverage(summary.totals, 'reasoning') }}</p>
              <p>{{ reviewFacts(summary.totals) }}</p>
            </div>
          </aside>
        </div>
        <div class="flex flex-wrap items-center gap-4">
          <span class="text-xs text-fg-muted">Group by</span>
          <UiButtonGroup aria-label="Group agent usage">
            <UiButton
              v-for="group in groups"
              :key="group.value"
              size="sm"
              :variant="filters.group_by === group.value ? 'secondary' : 'ghost'"
              :aria-pressed="filters.group_by === group.value"
              @click="emit('filters', { group_by: group.value as ActivityGroup })"
            >
              {{ group.label }}
            </UiButton>
          </UiButtonGroup>
        </div>
        <DataTable
          :items="rows"
          :columns="columns"
          aria-label="Agent activity breakdown"
          max-height="24rem"
        >
          <template #cell:agent_name="{ row }">
            <details class="agent-activity__row-detail">
              <summary
                class="focus-ring cursor-pointer"
                :title="filters.group_by === 'model' ? row.model ?? 'Model not recorded' : row.agent_name ?? 'Agent not recorded'"
              >
                <span>{{ filters.group_by === 'model' ? row.model ?? 'Model not recorded' : row.agent_name ?? 'Agent not recorded' }}</span>
                <span
                  v-if="filters.group_by === 'agent_model'"
                  class="ml-2 text-fg-muted"
                >{{ row.model ?? 'Model not recorded' }}</span>
                <span
                  v-if="global && filters.group_by !== 'model'"
                  class="ml-2 text-xs text-fg-muted"
                >{{ row.project_name }} #{{ row.project_id }}</span>
              </summary>
              <div class="agent-activity__details">
                <p v-if="global">
                  {{ row.project_name ?? 'Projects' }} · {{ row.project_ids.map(id => `#${id}`).join(', ') }}
                </p>
                <p>{{ row.host_key ?? 'Host not recorded' }} · {{ row.selected_role ?? 'Role varies or is not recorded' }}</p>
                <p>{{ reviewFacts(row) }}</p>
              </div>
            </details>
          </template>
          <template #cell:input="{ row }">
            <span
              tabindex="0"
              class="focus-ring tabular-nums"
              :title="exactUsage(row, 'input')"
              :aria-label="exactUsage(row, 'input')"
            >{{ formatTokens(row.reported_usage.input) }}</span><p
              v-if="fieldCoverage(row, 'input')"
              class="text-2xs text-fg-muted"
            >
              {{ fieldCoverage(row, 'input') }}
            </p>
          </template>
          <template #cell:cache_read="{ row }">
            <span
              tabindex="0"
              class="focus-ring tabular-nums"
              :title="exactUsage(row, 'cache_read')"
              :aria-label="exactUsage(row, 'cache_read')"
            >{{ formatTokens(row.reported_usage.cache_read) }}</span><p
              v-if="fieldCoverage(row, 'cache_read')"
              class="text-2xs text-fg-muted"
            >
              {{ fieldCoverage(row, 'cache_read') }}
            </p>
          </template>
          <template #cell:output="{ row }">
            <span
              tabindex="0"
              class="focus-ring tabular-nums"
              :title="exactUsage(row, 'output')"
              :aria-label="exactUsage(row, 'output')"
            >{{ formatTokens(row.reported_usage.output) }}</span><p
              v-if="fieldCoverage(row, 'output')"
              class="text-2xs text-fg-muted"
            >
              {{ fieldCoverage(row, 'output') }}
            </p>
          </template>
        </DataTable>
      </template>
      <p class="text-2xs leading-5 text-fg-subtle">
        Updated {{ formatAbsoluteDateTime(summary.as_of) }} · Models reflect recorded selection. Attempts do not guarantee model execution; external terminal and planning sessions are excluded.
      </p>
    </template>
  </section>
</template>

<style scoped>
.agent-activity { min-width: 0; display: flex; flex-direction: column; gap: 16px; }
.agent-activity__header { display: flex; flex-wrap: wrap; align-items: end; justify-content: space-between; gap: 16px; }
.agent-activity__dates { display: flex; gap: 12px; }
.agent-activity__dates > * { width: 150px; }
.agent-activity__headline { display: grid; grid-template-columns: minmax(0, 3fr) minmax(180px, 1fr); align-items: center; gap: 24px; padding: 8px 4px; }
.agent-activity__attempts { border-left: 1px solid var(--color-border-default); padding-left: 24px; font-size: 13px; line-height: 20px; }
.agent-activity__analysis { display: grid; grid-template-columns: minmax(0, 1fr) minmax(230px, 29%); gap: 24px; padding: 20px; border: 1px solid var(--color-border-default); border-radius: var(--radius-lg); background: var(--color-bg-surface); }
.agent-activity__cache { border-left: 1px solid var(--color-border-default); padding-left: 24px; min-width: 0; }
.agent-activity__share { margin-top: 16px; color: var(--color-accent-primary); font-size: 40px; font-weight: 600; line-height: 46px; letter-spacing: -0.03em; }
.agent-activity__details-toggle { color: var(--color-accent-primary-fg); }
.agent-activity__split { margin: 16px 0 12px; font-size: 13px; }
.agent-activity__split > div { display: flex; justify-content: space-between; gap: 12px; padding: 9px 0; border-bottom: 1px solid var(--color-border-subtle); }
.agent-activity__split dt { color: var(--color-fg-muted); }
.agent-activity__split dd { margin: 0; font-weight: 600; font-variant-numeric: tabular-nums; }
.agent-activity__details { display: grid; gap: 8px; white-space: normal; font-size: 12px; line-height: 18px; color: var(--color-fg-muted); padding: 12px 0; max-width: 34rem; }
.agent-activity__row-detail summary { max-width: 30rem; overflow-wrap: anywhere; white-space: normal; }
@media (max-width: 1100px) { .agent-activity__headline { grid-template-columns: minmax(0, 1fr); gap: 12px; } .agent-activity__attempts { border: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 4px 12px; } }
@media (max-width: 900px) { .agent-activity__analysis { grid-template-columns: minmax(0, 1fr); gap: 16px; padding: 16px; } .agent-activity__cache { border-left: 0; border-top: 1px solid var(--color-border-default); padding: 16px 0 0; } .agent-activity__share { margin-top: 8px; } }
</style>
