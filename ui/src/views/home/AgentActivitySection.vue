<script setup lang="ts">
import { computed, ref } from 'vue'
import DataTable from '@/components/DataTable.vue'
import type { DataTableColumn } from '@/components/types'
import AgentUsageSummary from '@/components/domain/AgentUsageSummary.vue'
import { UiButton, UiButtonGroup, UiCallout, UiFormField, UiInput, UiSkeleton } from '@/components/ui'
import { formatAbsoluteDateTime } from '@/lib/stackos/time'
import AgentActivityChart from './AgentActivityChart.vue'
import { agentLabel, cacheShare, exactUsage, formatTokens, reviewFacts, usageFields, type AgentStats, type AgentStatsFilters, type ActivityGroup, type ActivityView } from '@/lib/agentActivity'

const props = defineProps<{ summary: AgentStats | null; loading: boolean; error: string | null; filters: AgentStatsFilters; global?: boolean }>()
const emit = defineEmits<{ filters: [value: Partial<AgentStatsFilters>] }>()
const detailsOpen = ref(false)
const chartView = ref<ActivityView>('tokens')
const groups = [{ value: 'agent', label: 'Agent' }, { value: 'model', label: 'Model' }, { value: 'agent_model', label: 'Agent + model' }] as const
const rows = computed(() => props.summary?.rows.map(row => ({ ...row, id: row.group_key, input: row.reported_usage.input, cache_read: row.reported_usage.cache_read, output: row.reported_usage.output })) ?? [])
type Row = (typeof rows.value)[number]
const fields = computed(() => usageFields.filter(field => rows.value.some(row => row.reported_usage[field.key] != null)))
const columns = computed<DataTableColumn<Row>[]>(() => [
  { key: 'agent_name', label: props.filters.group_by === 'model' ? 'Selected model' : props.filters.group_by === 'agent' ? 'Agent' : 'Agent + model' },
  { key: 'launch_attempts', label: 'Attempts', cellClass: 'tabular-nums whitespace-nowrap' },
  ...fields.value.map(field => ({ key: field.key, label: field.label, cellClass: 'tabular-nums whitespace-nowrap' })),
])
const share = computed(() => props.summary ? cacheShare(props.summary.totals) : null)
const optionalFields = [
  { key: 'cache_write', label: 'Cache write', note: 'included in Input' },
  { key: 'reasoning', label: 'Reasoning', note: 'included in Output' },
] as const
const detailsAvailable = computed(() => !!props.summary && (optionalFields.some(field => props.summary!.totals.reported_usage[field.key] != null) || !!reviewFacts(props.summary.totals)))
const inputAvailable = computed(() => !!props.summary && (props.summary.totals.reported_usage.cache_read != null || props.summary.totals.reported_usage.remaining_input != null))
const chartAvailable = computed(() => props.summary?.daily.some(day => day.reported_usage.total != null && day.launch_attempts > 0))
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
        v-if="summary.totals.launch_attempts"
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
          <p
            v-if="summary.totals.reported_usage.cache_read != null"
            class="mt-1 text-xs text-fg-muted"
          >
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
        <div
          v-if="chartAvailable"
          class="agent-activity__analysis"
          :class="{ 'agent-activity__analysis--with-input': inputAvailable }"
        >
          <AgentActivityChart
            v-model:view="chartView"
            :days="summary.daily"
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
              Input reused from cache.
            </p>
            <dl class="agent-activity__split">
              <div v-if="summary.totals.reported_usage.cache_read != null">
                <dt>Cache</dt><dd :title="exactUsage(summary.totals, 'cache_read')">
                  {{ formatTokens(summary.totals.reported_usage.cache_read) }}
                </dd>
              </div>
              <div v-if="summary.totals.reported_usage.remaining_input != null">
                <dt>New input</dt><dd :title="formatTokens(summary.totals.reported_usage.remaining_input, false)">
                  {{ formatTokens(summary.totals.reported_usage.remaining_input) }}
                </dd>
              </div>
            </dl>
            <p class="text-xs leading-5 text-fg-muted">
              Cache is included in Input. Output is counted separately.
            </p>
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
              <p v-if="summary.totals.reported_usage[field.key] != null">
                <strong>{{ field.label }}:</strong> {{ formatTokens(summary.totals.reported_usage[field.key], false) }} · {{ field.note }}
              </p>
            </template>
            <p v-if="reviewFacts(summary.totals)">
              {{ reviewFacts(summary.totals) }}
            </p>
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
            <details class="agent-activity__row-detail">
              <summary
                class="focus-ring cursor-pointer"
                :title="filters.group_by === 'model' ? row.model ?? 'Model not recorded' : agentLabel(row.agent_name)"
              >
                <span>{{ filters.group_by === 'model' ? row.model ?? 'Model not recorded' : agentLabel(row.agent_name) }}</span>
                <span
                  v-if="filters.group_by === 'agent_model' && row.model"
                  class="ml-2 text-fg-muted"
                >{{ row.model }}</span>
                <span
                  v-if="global && filters.group_by !== 'model'"
                  class="ml-2 text-xs text-fg-muted"
                >{{ row.project_name }} #{{ row.project_id }}</span>
              </summary>
              <div class="agent-activity__details">
                <p v-if="global">
                  {{ row.project_name ?? 'Projects' }} · {{ row.project_ids.map(id => `#${id}`).join(', ') }}
                </p>
                <p v-if="filters.group_by === 'agent' && row.model">
                  {{ row.model }}
                </p>
                <p v-if="row.host_key">
                  {{ row.host_key === 'codex' ? 'Codex' : row.host_key }}
                </p>
                <p v-if="reviewFacts(row)">
                  {{ reviewFacts(row) }}
                </p>
              </div>
            </details>
          </template>
          <template
            v-for="field in fields"
            :key="field.key"
            #[`cell:${field.key}`]="{ row }"
          >
            <span
              v-if="row.reported_usage[field.key] != null"
              tabindex="0"
              class="focus-ring tabular-nums"
              :title="exactUsage(row, field.key)"
              :aria-label="exactUsage(row, field.key)"
            >{{ formatTokens(row.reported_usage[field.key]) }}</span>
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
.agent-activity__row-detail summary { overflow-wrap: anywhere; white-space: normal; }
.agent-activity :deep(table) { table-layout: fixed; width: 100%; }
.agent-activity :deep(th:first-child) { width: 40%; }
@media (max-width: 1100px) { .agent-activity__headline { grid-template-columns: minmax(0, 1fr); gap: 12px; } .agent-activity__attempts { border: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 4px 12px; } }
@media (max-width: 900px) { .agent-activity__analysis { grid-template-columns: minmax(0, 1fr); gap: 16px; padding: 16px; } .agent-activity__cache { border-left: 0; border-top: 1px solid var(--color-border-default); padding: 16px 0 0; } .agent-activity__share { margin-top: 8px; } }
</style>
