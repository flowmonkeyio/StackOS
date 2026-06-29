<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'

import type {
  SchemaOperationDescribeOut,
  SchemaOperationListOut,
  SchemaOperationSummaryOut,
} from '@/api'
import DataTable from '@/components/DataTable.vue'
import InspectableDetailDrawer from '@/components/InspectableDetailDrawer.vue'
import ProjectPageHeader from '@/components/domain/ProjectPageHeader.vue'
import {
  UiBadge,
  UiCallout,
  UiCountBadge,
  UiFactGroups,
  UiFilterBar,
  UiJsonBlock,
  UiPageShell,
  UiSectionHeader,
  UiSegmentedControl,
  UiSkeleton,
} from '@/components/ui'
import type { DataTableColumn } from '@/components/types'
import type { UiFactGroup } from '@/components/ui/UiFactGroups.vue'
import { apiFetch, formatApiError } from '@/lib/client'

type SurfaceFilter = 'all' | 'mcp' | 'rest' | 'cli'
type OperationRow = SchemaOperationSummaryOut & { id: string }

const route = useRoute()
const router = useRouter()

const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const rows = ref<OperationRow[]>([])
const selected = ref<SchemaOperationDescribeOut | null>(null)
const detailOpen = ref(false)
const loading = ref(false)
const detailLoading = ref(false)
const error = ref<string | null>(null)
const surfaceFilter = ref<SurfaceFilter>('all')
const search = ref('')

const surfaceOptions = [
  { key: 'all', label: 'All' },
  { key: 'mcp', label: 'MCP' },
  { key: 'rest', label: 'REST' },
  { key: 'cli', label: 'CLI' },
]

const visibleRows = computed<OperationRow[]>(() => {
  const query = search.value.trim().toLowerCase()
  if (!query) return rows.value
  return rows.value.filter((row) =>
    `${row.name} ${row.summary ?? ''}`.toLowerCase().includes(query),
  )
})

const columns: DataTableColumn<OperationRow>[] = [
  { key: 'name', label: 'Operation', widthClass: 'w-56', cellClass: 'font-mono text-xs' },
  { key: 'summary', label: 'Summary' },
  { key: 'surfaces', label: 'Surfaces', widthClass: 'w-40' },
  { key: 'grant_policy', label: 'Grant', widthClass: 'w-48' },
]

const selectedName = computed(() => String(route.query.operation ?? ''))
const selectedSurfaces = computed(() => (selected.value ? enabledSurfaceNames(selected.value) : []))
const operationFactGroups = computed<UiFactGroup[]>(() => {
  if (!selected.value) return []
  return [
    {
      title: 'When',
      items: [{ label: 'Use cases', value: listSummary(selected.value.when_to_use), wide: true }],
    },
    {
      title: 'Requires',
      items: [
        { label: 'Prerequisites', value: listSummary(selected.value.prerequisites), wide: true },
      ],
    },
    {
      title: 'Returns',
      items: [{ label: 'Output', value: listSummary(selected.value.returns), wide: true }],
    },
  ]
})

function enabledSurfaceNames(operation: SchemaOperationSummaryOut | SchemaOperationDescribeOut): string[] {
  return Object.entries(operation.surfaces)
    .filter(([, surface]) => surface.enabled)
    .map(([name]) => name)
}

function listSummary(items: string[] | undefined): string {
  return items?.length ? items.join(' · ') : ''
}

async function loadList(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const query = surfaceFilter.value === 'all' ? '' : `?surface=${surfaceFilter.value}`
    const payload = await apiFetch<SchemaOperationListOut>(`/api/v1/operations${query}`)
    rows.value = payload.items.map((item) => ({ ...item, id: item.name }))
    const requestedName = selectedName.value
    const name = requestedName || rows.value[0]?.name || ''
    if (name) await loadDetail(name)
    else selected.value = null
    if (requestedName) detailOpen.value = true
  } catch (err) {
    error.value = formatApiError(err)
  } finally {
    loading.value = false
  }
}

async function loadDetail(name: string): Promise<void> {
  if (!name) {
    selected.value = null
    return
  }
  detailLoading.value = true
  error.value = null
  try {
    selected.value = await apiFetch<SchemaOperationDescribeOut>(
      `/api/v1/operations/${encodeURIComponent(name)}`,
    )
  } catch (err) {
    error.value = formatApiError(err)
  } finally {
    detailLoading.value = false
  }
}

async function selectOperation(row: OperationRow): Promise<void> {
  detailOpen.value = true
  await router.replace({
    query: {
      ...route.query,
      operation: row.name,
    },
  })
}

function setSurface(value: string | number): void {
  surfaceFilter.value = String(value) as SurfaceFilter
  void loadList()
}

onMounted(loadList)
onBeforeRouteUpdate((to) => {
  const name = String(to.query.operation ?? '')
  detailOpen.value = Boolean(name)
  void loadDetail(name)
})
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Operations"
      description="Protocol-neutral contracts exposed through MCP, REST, and CLI."
      :breadcrumbs="[{ label: 'Operations' }]"
    />

    <UiCallout
      v-if="error"
      tone="danger"
    >
      {{ error }}
    </UiCallout>

    <section
      aria-label="Operations catalog"
      class="space-y-3"
    >
      <UiSectionHeader
        title="Catalog"
        as="h3"
      >
        <template #actions>
          <UiCountBadge :value="visibleRows.length" />
        </template>
      </UiSectionHeader>
      <UiFilterBar
        v-model:search="search"
        search-placeholder="Find by name or summary…"
        aria-label="Operation filters"
      >
        <UiSegmentedControl
          :model-value="surfaceFilter"
          :options="surfaceOptions"
          label="Surface"
          @select="setSurface"
        />
      </UiFilterBar>
      <DataTable
        :items="visibleRows"
        :columns="columns"
        :loading="loading"
        :selected-id="selected?.name"
        aria-label="StackOS operations"
        empty-message="No operations match — operations are registered by StackOS core and plugins."
        interactive
        @row-click="selectOperation"
      >
        <template #cell:surfaces="{ row }">
          <span class="flex flex-wrap gap-1">
            <UiBadge
              v-for="surface in enabledSurfaceNames(row)"
              :key="surface"
              variant="outline"
            >
              {{ surface }}
            </UiBadge>
          </span>
        </template>
        <template #cell:grant_policy="{ value }">
          <UiBadge variant="outline">
            {{ value }}
          </UiBadge>
        </template>
      </DataTable>
    </section>

    <InspectableDetailDrawer
      v-model="detailOpen"
      :title="selected?.name ?? 'Operation'"
      :description="selected?.summary"
      size="xl"
      :has-detail="Boolean(selected) || detailLoading"
      empty-title="No operation selected"
      empty-description="Select an operation row to inspect schemas, grants, examples, and surface policy."
    >
      <template #header="{ titleId, descriptionId }">
        <div class="min-w-0">
          <div class="flex flex-wrap items-center gap-2">
            <h2
              :id="titleId"
              class="t-h2 text-fg-strong"
            >
              {{ selected?.name ?? 'Operation' }}
            </h2>
            <UiBadge
              v-if="selected"
              :tone="selected.read_only ? 'success' : 'warning'"
              variant="outline"
            >
              {{ selected.read_only ? 'read' : 'write' }}
            </UiBadge>
            <UiBadge
              v-for="surface in selectedSurfaces"
              :key="surface"
              variant="outline"
            >
              {{ surface }}
            </UiBadge>
          </div>
          <p
            v-if="selected"
            :id="descriptionId"
            class="mt-1 text-sm text-fg-muted"
          >
            {{ selected.summary }}
          </p>
        </div>
      </template>

      <div
        v-if="detailLoading"
        class="space-y-3"
        aria-busy="true"
        aria-label="Loading operation"
      >
        <UiSkeleton
          width="50%"
          :height="16"
        />
        <UiSkeleton
          width="75%"
          :height="16"
        />
        <UiSkeleton
          shape="block"
          :height="128"
        />
      </div>
      <div
        v-else-if="selected"
        class="space-y-5"
      >
        <div class="space-y-2">
          <p class="text-sm text-fg-muted">
            {{ selected.summary }}
          </p>
          <p class="text-sm text-fg-default">
            {{ selected.purpose }}
          </p>
        </div>

        <UiFactGroups
          :groups="operationFactGroups"
          aria-label="Operation summary facts"
        />

        <section
          v-if="selected.examples?.length"
          class="space-y-3"
        >
          <h4 class="text-sm font-semibold text-fg-strong">
            Examples
          </h4>
          <div class="grid gap-3 md:grid-cols-2">
            <article
              v-for="example in selected.examples"
              :key="example.title"
              class="rounded-md border border-subtle bg-bg-surface p-3"
            >
              <p class="mb-2 text-sm font-medium text-fg-strong">
                {{ example.title }}
              </p>
              <UiJsonBlock
                :data="example.arguments"
                density="compact"
                max-height="18rem"
              />
            </article>
          </div>
        </section>

        <div class="grid gap-4 lg:grid-cols-2">
          <section class="space-y-2">
            <h4 class="text-sm font-semibold text-fg-strong">
              Input schema
            </h4>
            <UiJsonBlock
              :data="selected.input_schema"
              density="compact"
              max-height="24rem"
            />
          </section>
          <section class="space-y-2">
            <h4 class="text-sm font-semibold text-fg-strong">
              Output schema
            </h4>
            <UiJsonBlock
              :data="selected.output_schema"
              density="compact"
              max-height="24rem"
            />
          </section>
        </div>
      </div>
    </InspectableDetailDrawer>
  </UiPageShell>
</template>
