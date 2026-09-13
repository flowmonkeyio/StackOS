<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import DataTable from '@/components/DataTable.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import {
  UiButton,
  UiCallout,
  UiCard,
  UiEmptyState,
  UiInput,
  UiSectionHeader,
  UiSelect,
} from '@/components/ui'
import type { DataTableColumn } from '@/components/types'
import { GETTING_STARTED_URL } from '@/lib/externalLinks'
import { trackerStatus } from '@/design/status'
import { ticketStatuses, ticketWorkUrl, type PortfolioProject } from './ticketOverview'
import type { PortfolioFilters } from './useHomePortfolioInsights'

const props = defineProps<{
  items: PortfolioProject[]
  loading: boolean
  error: string | null
  nextCursor: number | null
  total: number | null
  filters: PortfolioFilters
}>()
const emit = defineEmits<{
  filters: [value: PortfolioFilters]
  more: []
  clearStatus: []
}>()
const router = useRouter()
const search = ref('')
const sort = ref<'recent' | 'name'>('recent')
const columns: DataTableColumn<PortfolioProject>[] = [
  { key: 'name', label: 'Project', widthClass: 'w-64' },
  { key: 'latest_task', label: 'Latest tracked work' },
  { key: 'ticket_count', label: 'Tickets', widthClass: 'w-24' },
  { key: 'ticket_counts', label: 'Ticket status', widthClass: 'w-80' },
]
function applyFilters(): void {
  emit('filters', {
    ...props.filters,
    query: search.value.trim(),
    sort: sort.value,
  })
}
function changeSort(value: string | number | null): void {
  sort.value = value === 'name' ? 'name' : 'recent'
  applyFilters()
}
</script>

<template>
  <UiCard
    section
    aria-label="Projects"
  >
    <UiSectionHeader title="Projects">
      <template #actions>
        <form
          class="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-2 sm:w-auto sm:grid-cols-[13rem_auto_10rem]"
          @submit.prevent="applyFilters"
        >
          <UiInput
            v-model="search"
            size="sm"
            placeholder="Search projects"
            aria-label="Search projects"
          />
          <UiButton
            type="submit"
            size="sm"
            variant="secondary"
          >
            Search
          </UiButton>
          <div class="col-span-2 sm:col-span-1">
            <UiSelect
              :model-value="sort"
              :options="[
                { value: 'recent', label: 'Recent activity' },
                { value: 'name', label: 'Name' },
              ]"
              size="sm"
              aria-label="Sort projects"
              @update:model-value="changeSort"
            />
          </div>
        </form>
      </template>
    </UiSectionHeader>
    <div class="mt-3 flex items-center justify-between gap-3">
      <div
        v-if="filters.ticket_status"
        class="flex flex-wrap items-center gap-2 text-xs text-fg-muted"
      >
        Projects with {{ trackerStatus[filters.ticket_status].label.toLowerCase() }} tickets
        <UiButton
          variant="ghost"
          size="sm"
          aria-label="Clear ticket status filter"
          @click="emit('clearStatus')"
        >
          Clear
        </UiButton>
      </div>
      <p class="text-2xs text-fg-subtle">
        {{ items.length }} loaded<span v-if="total !== null"> of {{ total }}</span>
      </p>
    </div>
    <UiCallout
      v-if="error"
      tone="warning"
      class="mt-3"
    >
      {{ error }}<span v-if="items.length"> Previously loaded projects are shown.</span>
    </UiCallout>
    <UiEmptyState
      v-if="!loading && !error && !items.length && !search && filters.is_active === true && !filters.ticket_status"
      title="Ready for your first project"
      description="Open a project in your AI tool and ask the agent to connect it to StackOS."
      icon="folder"
      class="mt-3"
    >
      <template #actions>
        <UiButton
          :href="GETTING_STARTED_URL"
          target="_blank"
          rel="noopener noreferrer"
        >
          Open getting started
        </UiButton>
      </template>
    </UiEmptyState>
    <DataTable
      v-else
      :items="items"
      :columns="columns"
      :loading="loading"
      aria-label="Project portfolio"
      empty-message="No projects match these filters."
      interactive
      class="mt-3"
      @row-click="router.push(`/projects/${$event.id}`)"
    >
      <template #cell:name="{ row }">
        <div class="min-w-0">
          <span class="font-medium text-fg-strong">{{ row.name }}</span>
          <p class="truncate text-xs text-fg-muted">
            {{ row.domain || row.slug }}
          </p>
        </div>
      </template>
      <template #cell:latest_task="{ row }">
        <UiButton
          v-if="row.latest_task"
          :href="`/projects/${row.id}/tasks?task=${encodeURIComponent(row.latest_task.key)}`"
          variant="ghost"
          size="sm"
          @click.stop.prevent="
            router.push(`/projects/${row.id}/tasks?task=${encodeURIComponent(row.latest_task.key)}`)
          "
        >
          {{ row.latest_task.title }}
        </UiButton><StatusBadge
          v-if="row.latest_task"
          kind="tracker"
          :status="row.latest_task.status"
        /><span
          v-else
          class="text-fg-subtle"
        >No tracked work</span>
      </template>
      <template #cell:ticket_count="{ row }">
        <UiButton
          :href="ticketWorkUrl(row.id)"
          variant="ghost"
          size="sm"
          @click.stop.prevent="router.push(ticketWorkUrl(row.id))"
        >
          {{ row.ticket_count }}
        </UiButton>
      </template>
      <template #cell:ticket_counts="{ row }">
        <div
          v-if="row.ticket_count"
          class="flex flex-wrap gap-1"
        >
          <template
            v-for="status in ticketStatuses"
            :key="status"
          >
            <UiButton
              v-if="row.ticket_counts[status]"
              :href="ticketWorkUrl(row.id, status)"
              variant="ghost"
              size="sm"
              @click.stop.prevent="router.push(ticketWorkUrl(row.id, status))"
            >
              {{ trackerStatus[status].label }} {{ row.ticket_counts[status] }}
            </UiButton>
          </template>
        </div>
        <span
          v-else
          class="text-fg-subtle"
        >No tickets</span>
      </template>
    </DataTable>
    <UiButton
      v-if="nextCursor !== null"
      class="mt-3"
      :loading="loading"
      @click="emit('more')"
    >
      Load more projects
    </UiButton>
  </UiCard>
</template>
