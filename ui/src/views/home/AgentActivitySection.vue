<script setup lang="ts">
import { computed, ref } from 'vue'
import DataTable from '@/components/DataTable.vue'
import type { DataTableColumn } from '@/components/types'
import AgentUsageSummary from '@/components/domain/AgentUsageSummary.vue'
import { UiButton, UiButtonGroup, UiCallout, UiFormField, UiInput, UiSkeleton } from '@/components/ui'
import { formatAbsoluteDateTime } from '@/lib/stackos/time'
import AgentActivityChart from './AgentActivityChart.vue'
import { activityColor, agentLabel, cacheShare, exactUsage, formatTokens, formatUsage, groupLabel, reviewFacts, usageFields, usageValue, type AgentStats, type AgentStatsFilters, type ActivityGroup, type ActivityView } from '@/lib/agentActivity'

const props = defineProps<{ summary: AgentStats | null; loading: boolean; error: string | null; filters: AgentStatsFilters; global?: boolean }>()
const emit = defineEmits<{ filters: [value: Partial<AgentStatsFilters>] }>()
const detailsOpen = ref(false)
const chartView = ref<ActivityView>('tokens')
const groups = [{ value: 'agent', label: 'Agent' }, { value: 'model', label: 'Model' }, { value: 'agent_model', label: 'Agent + model' }] as const
const displayGroup = computed(() => props.summary?.group_by ?? props.filters.group_by)
const rows = computed(() => props.summary?.rows.map(row => ({
  ...row, id: row.group_key, input: usageValue(row, 'input'), cache_read: usageValue(row, 'cache_read'), output: usageValue(row, 'output'),
  review_facts: reviewFacts(row),
})) ?? [])
type Row = (typeof rows.value)[number]
const fields = computed(() => usageFields.filter(field => rows.value.some(row => usageValue(row, field.key) != null)))
const columns = computed<DataTableColumn<Row>[]>(() => [
  { key: 'agent_name', label: displayGroup.value === 'model' ? 'Selected model' : displayGroup.value === 'agent' ? 'Agent' : 'Agent + model' },
  { key: 'launch_attempts', label: 'Attempts', cellClass: 'tabular-nums whitespace-nowrap' },
  ...fields.value.map(field => ({ key: field.key, label: field.label, cellClass: 'tabular-nums whitespace-nowrap' })),
])
const share = computed(() => props.summary ? cacheShare(props.summary.totals) : null)
const optionalFields = [
  { key: 'cache_write', label: 'Cache write', note: 'included in Input' },
  { key: 'reasoning', label: 'Reasoning', note: 'included in Output' },
] as const
const detailsAvailable = computed(() => optionalFields.some(field => usageValue(props.summary?.totals, field.key) != null))
const inputAvailable = computed(() => usageValue(props.summary?.totals, 'cache_read') != null || usageValue(props.summary?.totals, 'input') != null)
const chartAvailable = computed(() => props.summary?.daily.some(day => usageValue(day, 'total') != null))
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
        </h2>
        <p class="mt-1 text-xs text-fg-muted">
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
      <div
        class="agent-activity__headline"
      >
        <AgentUsageSummary :metrics="summary.totals" />
        <div class="agent-activity__attempts">
          <p><strong>{{ summary.totals.launch_attempts.toLocaleString('en-US') }}</strong> agent {{ summary.totals.launch_attempts === 1 ? 'attempt' : 'attempts' }}</p>
          <p
            v-if="summary.totals.coverage.usage_unavailable_attempts"
            class="text-xs text-fg-muted"
          >
            Usage missing for {{ summary.totals.coverage.usage_unavailable_attempts }} {{ summary.totals.coverage.usage_unavailable_attempts === 1 ? 'attempt' : 'attempts' }}
          </p>
        </div>
      </div>
      <p
        v-if="reviewFacts(summary.totals)"
        class="text-xs text-fg-muted"
        aria-label="Review outcomes"
      >
        {{ reviewFacts(summary.totals) }}
      </p>
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
        <div
          v-if="chartAvailable"
          class="agent-activity__analysis"
          :class="{ 'agent-activity__analysis--with-input': inputAvailable }"
        >
          <AgentActivityChart
            v-model:view="chartView"
            :days="summary.daily"
            :group="displayGroup"
            :timezone="filters.timezone"
            :global="global"
          />
          <aside
            v-if="inputAvailable"
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
              v-if="share"
              class="mt-1 text-xs text-fg-muted"
            >
              Reused from cache.
            </p>
            <dl class="agent-activity__split">
              <template
                v-for="field in usageFields.filter(item => item.key !== 'output')"
                :key="field.key"
              >
                <div v-if="usageValue(summary.totals, field.key) != null">
                  <dt>{{ field.label }}</dt><dd
                    :title="exactUsage(summary.totals, field.key)"
                    :aria-label="exactUsage(summary.totals, field.key)"
                    tabindex="0"
                  >
                    {{ formatTokens(usageValue(summary.totals, field.key)) }}
                  </dd>
                </div>
              </template>
            </dl>
          </aside>
        </div>
        <div v-if="detailsAvailable">
          <UiButton
            variant="ghost"
            size="sm"
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
            <template
              v-for="field in optionalFields"
              :key="field.key"
            >
              <p v-if="usageValue(summary.totals, field.key) != null">
                <strong>{{ field.label }}:</strong> {{ formatTokens(usageValue(summary.totals, field.key), false) }} · {{ field.note }}
              </p>
            </template>
          </div>
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
            <div class="agent-activity__identity">
              <span
                class="agent-activity__swatch"
                :style="{ backgroundColor: `var(--color-${activityColor(row.group_key)})` }"
                aria-hidden="true"
              />
              <div
                class="agent-activity__row-label"
                :title="groupLabel(row, displayGroup, global)"
              >
                <span
                  class="block truncate"
                  :title="displayGroup === 'model' ? row.model ?? 'Model not recorded' : agentLabel(row.agent_name)"
                >{{ displayGroup === 'model' ? row.model ?? 'Model not recorded' : agentLabel(row.agent_name) }}</span>
                <span
                  v-if="displayGroup === 'agent_model'"
                  class="block truncate text-xs text-fg-muted"
                  :title="row.model ?? 'Model not recorded'"
                >{{ row.model ?? 'Model not recorded' }}</span>
                <span
                  v-if="global && displayGroup !== 'model'"
                  class="block truncate text-xs text-fg-muted"
                  :title="row.project_name ?? `Project #${row.project_id}`"
                >{{ row.project_name ?? `Project #${row.project_id}` }}</span>
                <p
                  v-if="row.review_facts"
                  class="mt-1 truncate text-xs text-fg-muted"
                  :title="row.review_facts"
                >
                  {{ row.review_facts }}
                </p>
              </div>
            </div>
          </template>
          <template
            v-for="field in fields"
            :key="field.key"
            #[`cell:${field.key}`]="{ row }"
          >
            <span
              tabindex="0"
              class="focus-ring tabular-nums"
              :title="exactUsage(row, field.key)"
              :aria-label="exactUsage(row, field.key)"
              :class="{ 'text-fg-muted': usageValue(row, field.key) == null }"
            >{{ formatUsage(usageValue(row, field.key)) }}</span>
          </template>
        </DataTable>
      </template>
      <p class="text-2xs leading-5 text-fg-subtle">
        Updated {{ formatAbsoluteDateTime(summary.as_of) }} · Recorded model selections. External terminal and planning sessions are excluded.
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
.agent-activity__analysis { display: grid; grid-template-columns: minmax(0, 1fr); gap: 24px; padding: 20px; border: 1px solid var(--color-border-default); border-radius: var(--radius-lg); background: var(--color-bg-surface); }
.agent-activity__analysis--with-input { grid-template-columns: minmax(0, 1fr) minmax(210px, 27%); }
.agent-activity__cache { border-left: 1px solid var(--color-border-default); padding-left: 24px; min-width: 0; }
.agent-activity__share { margin-top: 16px; color: var(--color-accent-primary); font-size: 40px; font-weight: 600; line-height: 46px; letter-spacing: -0.03em; }
.agent-activity__split { margin: 16px 0 12px; font-size: 13px; }
.agent-activity__split > div { display: flex; justify-content: space-between; gap: 12px; padding: 9px 0; border-bottom: 1px solid var(--color-border-subtle); }
.agent-activity__split dt { color: var(--color-fg-muted); }
.agent-activity__split dd { margin: 0; font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
.agent-activity__details { display: grid; gap: 8px; white-space: normal; font-size: 12px; line-height: 18px; color: var(--color-fg-muted); padding: 12px 0; max-width: 34rem; overflow-wrap: anywhere; }
.agent-activity__row-label { overflow-wrap: anywhere; white-space: normal; }
.agent-activity__identity { display: flex; align-items: start; gap: 6px; }
.agent-activity__identity > .agent-activity__row-label { min-width: 0; }
.agent-activity__swatch { width: 10px; height: 10px; margin-top: 4px; border-radius: 2px; flex: none; }
.agent-activity :deep(.cs-datatable-wrapper > .overflow-x-auto) { scrollbar-gutter: stable; }
.agent-activity :deep(table) { table-layout: fixed; width: 100%; }
.agent-activity :deep(th:first-child) { width: 40%; }
@media (max-width: 1100px) { .agent-activity__headline { grid-template-columns: minmax(0, 1fr); gap: 12px; } .agent-activity__attempts { border: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 4px 12px; } }
@media (max-width: 900px) { .agent-activity__analysis { grid-template-columns: minmax(0, 1fr); gap: 16px; padding: 16px; } .agent-activity__cache { border-left: 0; border-top: 1px solid var(--color-border-default); padding: 16px 0 0; } .agent-activity__share { margin-top: 8px; } }
</style>
