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
  UiJsonBlock,
  UiPageShell,
  UiPanel,
  UiSectionHeader,
  UiSegmentedControl,
} from '@/components/ui'
import type { DataTableColumn } from '@/components/types'
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

const surfaceOptions = [
  { key: 'all', label: 'All' },
  { key: 'mcp', label: 'MCP' },
  { key: 'rest', label: 'REST' },
  { key: 'cli', label: 'CLI' },
]

const columns: DataTableColumn<OperationRow>[] = [
  { key: 'name', label: 'Operation', widthClass: 'w-56' },
  { key: 'summary', label: 'Summary' },
  { key: 'surfaces', label: 'Surfaces', widthClass: 'w-40' },
  { key: 'grant_policy', label: 'Grant', widthClass: 'w-48' },
]

const selectedName = computed(() => String(route.query.operation ?? ''))
const selectedSurfaces = computed(() => (selected.value ? enabledSurfaceNames(selected.value) : []))

function enabledSurfaceNames(operation: SchemaOperationSummaryOut | SchemaOperationDescribeOut): string[] {
  return Object.entries(operation.surfaces)
    .filter(([, surface]) => surface.enabled)
    .map(([name]) => name)
}

function policyTone(policy: string): 'neutral' | 'accent' | 'success' | 'warning' | 'danger' {
  if (policy.includes('admin')) return 'warning'
  if (policy.includes('controller') || policy.includes('step')) return 'accent'
  if (policy.includes('read')) return 'success'
  return 'neutral'
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

    <UiPanel class="p-4">
      <UiSectionHeader
        title="Catalog"
        as="h3"
      >
        <template #actions>
          <UiSegmentedControl
            :model-value="surfaceFilter"
            :options="surfaceOptions"
            label="Surface"
            @select="setSurface"
          />
          <UiBadge>{{ rows.length }}</UiBadge>
        </template>
      </UiSectionHeader>

      <DataTable
        :items="rows"
        :columns="columns"
        :loading="loading"
        :selected-id="selected?.name"
        max-height="calc(100vh - 18rem)"
        aria-label="StackOS operations"
        empty-message="No operations."
        interactive
        @row-click="selectOperation"
      >
        <template #cell:name="{ value }">
          <span class="font-medium text-fg-strong">{{ value }}</span>
        </template>
        <template #cell:surfaces="{ row }">
          <span class="flex flex-wrap gap-1">
            <UiBadge
              v-for="surface in enabledSurfaceNames(row)"
              :key="surface"
              tone="accent"
            >
              {{ surface }}
            </UiBadge>
          </span>
        </template>
        <template #cell:grant_policy="{ value }">
          <UiBadge :tone="policyTone(String(value))">
            {{ value }}
          </UiBadge>
        </template>
      </DataTable>
    </UiPanel>

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
              class="t-h1 text-fg-strong"
            >
              {{ selected?.name ?? 'Operation' }}
            </h2>
            <UiBadge
              v-if="selected"
              :tone="selected.read_only ? 'success' : 'warning'"
            >
              {{ selected.read_only ? 'read' : 'write' }}
            </UiBadge>
            <UiBadge
              v-for="surface in selectedSurfaces"
              :key="surface"
              tone="accent"
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
        class="py-8 text-center text-sm text-fg-muted"
      >
        Loading…
      </div>
      <div
        v-else-if="selected"
        class="space-y-5"
      >
        <div class="space-y-2">
          <p class="text-sm text-fg-muted">{{ selected.summary }}</p>
          <p class="text-sm text-fg-default">{{ selected.purpose }}</p>
        </div>

        <div class="grid gap-4 lg:grid-cols-3">
          <section class="space-y-2 rounded-md border border-subtle p-3">
            <h4 class="text-sm font-semibold text-fg-strong">When</h4>
            <ul class="space-y-1 text-sm text-fg-muted">
              <li
                v-for="item in selected.when_to_use ?? []"
                :key="item"
              >
                {{ item }}
              </li>
            </ul>
          </section>
          <section class="space-y-2 rounded-md border border-subtle p-3">
            <h4 class="text-sm font-semibold text-fg-strong">Requires</h4>
            <ul class="space-y-1 text-sm text-fg-muted">
              <li
                v-for="item in selected.prerequisites ?? []"
                :key="item"
              >
                {{ item }}
              </li>
            </ul>
          </section>
          <section class="space-y-2 rounded-md border border-subtle p-3">
            <h4 class="text-sm font-semibold text-fg-strong">Returns</h4>
            <ul class="space-y-1 text-sm text-fg-muted">
              <li
                v-for="item in selected.returns ?? []"
                :key="item"
              >
                {{ item }}
              </li>
            </ul>
          </section>
        </div>

        <section
          v-if="selected.examples?.length"
          class="space-y-3"
        >
          <h4 class="text-sm font-semibold text-fg-strong">Examples</h4>
          <div class="grid gap-3 md:grid-cols-2">
            <article
              v-for="example in selected.examples"
              :key="example.title"
              class="rounded-md border border-subtle p-3"
            >
              <p class="mb-2 text-sm font-medium text-fg-strong">{{ example.title }}</p>
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
            <h4 class="text-sm font-semibold text-fg-strong">Input Schema</h4>
            <UiJsonBlock
              :data="selected.input_schema"
              density="compact"
              max-height="24rem"
            />
          </section>
          <section class="space-y-2">
            <h4 class="text-sm font-semibold text-fg-strong">Output Schema</h4>
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
