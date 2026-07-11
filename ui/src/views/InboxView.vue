<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ProjectPageHeader } from '@/components/domain'
import {
  UiBadge,
  UiButton,
  UiCard,
  UiEmptyState,
  UiIcon,
  UiPageShell,
  UiSkeleton,
} from '@/components/ui'
import { usePolling } from '@/composables/usePolling'
import { formatAbsoluteDateTime, formatRelativeDateTime } from '@/lib/stackos/time'
import { useAttentionStore, type AttentionItem, type AttentionKind } from '@/stores/attention'

type AttentionFilter = 'all' | AttentionKind

const route = useRoute()
const router = useRouter()
const projectId = computed(() => Number.parseInt(route.params.id as string, 10))
const base = computed(() => `/projects/${projectId.value}`)

const attention = useAttentionStore()
const loaded = ref(false)
const activeFilter = ref<AttentionFilter>('all')
const selectedId = ref(typeof route.query.item === 'string' ? route.query.item : '')

const FILTERS: { kind: AttentionFilter; label: string }[] = [
  { kind: 'all', label: 'All' },
  { kind: 'question', label: 'Questions' },
  { kind: 'blocked', label: 'Blocked' },
  { kind: 'failed-run', label: 'Failures' },
  { kind: 'connection', label: 'Setup' },
  { kind: 'budget', label: 'Spend' },
]

const KIND_LABEL: Record<AttentionKind, string> = {
  question: 'Question',
  blocked: 'Blocked work',
  'failed-run': 'Failed run',
  connection: 'Setup issue',
  budget: 'Spend alert',
}

const KIND_ICON: Record<AttentionKind, string> = {
  question: 'inbox',
  blocked: 'octagon-alert',
  'failed-run': 'x-circle',
  connection: 'plug',
  budget: 'banknotes',
}

const visibleItems = computed(() =>
  activeFilter.value === 'all'
    ? attention.items
    : attention.items.filter((item) => item.kind === activeFilter.value),
)

const selectedItem = computed<AttentionItem | null>(() => {
  const exact = visibleItems.value.find((item) => item.id === selectedId.value)
  return exact ?? visibleItems.value[0] ?? null
})

function filterCount(kind: AttentionFilter): number {
  return kind === 'all' ? attention.total : attention.countsByKind[kind]
}

function setFilter(kind: AttentionFilter): void {
  activeFilter.value = kind
  selectedId.value = ''
  void router.replace({ query: { ...route.query, item: undefined } })
}

function selectItem(item: AttentionItem): void {
  selectedId.value = item.id
  void router.replace({ query: { ...route.query, item: item.id } })
}

function openSelected(): void {
  if (selectedItem.value) void router.push(selectedItem.value.to)
}

function openRelatedActivity(item: AttentionItem): void {
  const view = item.kind === 'connection' || item.kind === 'budget' ? 'setup' : 'work'
  void router.push(`${base.value}/activity?view=${view}`)
}

async function load(): Promise<void> {
  const id = projectId.value
  if (!id || Number.isNaN(id)) return
  await attention.refresh(id)
  loaded.value = true
}

const { lastRunAt, refresh } = usePolling(load, { intervalMs: 20_000 })

const manualBusy = ref(false)
async function manualRefresh(): Promise<void> {
  manualBusy.value = true
  try {
    await refresh()
  } finally {
    manualBusy.value = false
  }
}

const updatedLabel = computed(() =>
  lastRunAt.value ? formatRelativeDateTime(lastRunAt.value.toISOString()) : null,
)
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Attention"
      description="Human decisions, questions, failures, and setup issues—ranked by impact."
      :breadcrumbs="[{ label: 'Attention' }]"
    >
      <template #actions>
        <span
          v-if="updatedLabel"
          class="hidden text-2xs text-fg-subtle sm:inline"
          :title="lastRunAt ? formatAbsoluteDateTime(lastRunAt.toISOString()) : undefined"
        >
          Updated {{ updatedLabel }}
        </span>
        <UiButton
          variant="secondary"
          size="sm"
          icon-left="refresh"
          :loading="manualBusy"
          @click="manualRefresh"
        >
          Refresh
        </UiButton>
      </template>
    </ProjectPageHeader>

    <div
      v-if="!loaded"
      class="grid gap-4 xl:grid-cols-[minmax(280px,0.8fr)_minmax(0,1.4fr)]"
    >
      <UiSkeleton
        shape="block"
        height="30rem"
      />
      <UiSkeleton
        shape="block"
        height="30rem"
      />
    </div>

    <UiCard
      v-else-if="attention.items.length === 0"
      section
    >
      <UiEmptyState
        icon="check-circle"
        title="Nothing needs you"
        description="Approvals, questions, failures, and setup issues appear here when a person is required."
      />
    </UiCard>

    <template v-else>
      <div
        class="flex flex-wrap items-center gap-1.5"
        aria-label="Attention filters"
      >
        <UiButton
          v-for="filter in FILTERS"
          :key="filter.kind"
          :variant="activeFilter === filter.kind ? 'secondary' : 'ghost'"
          size="sm"
          @click="setFilter(filter.kind)"
        >
          {{ filter.label }}
          <span class="ml-1 text-2xs text-fg-subtle">{{ filterCount(filter.kind) }}</span>
        </UiButton>
      </div>

      <div class="grid min-h-[34rem] gap-4 xl:grid-cols-[minmax(300px,0.8fr)_minmax(0,1.4fr)]">
        <UiCard
          :padded="false"
          class="overflow-hidden"
        >
          <div class="border-b border-subtle px-4 py-3">
            <p class="text-xs font-medium text-fg-muted">
              {{ visibleItems.length }} {{ visibleItems.length === 1 ? 'item' : 'items' }}
            </p>
          </div>
          <div
            v-if="visibleItems.length"
            class="divide-y divide-border-subtle"
          >
            <button
              v-for="item in visibleItems"
              :key="item.id"
              type="button"
              :class="[
                'focus-ring-inset flex w-full items-start gap-3 px-4 py-3 text-left transition-colors duration-fast',
                selectedItem?.id === item.id ? 'bg-accent-subtle' : 'hover:bg-bg-surface-alt',
              ]"
              @click="selectItem(item)"
            >
              <span
                :class="[
                  'mt-1.5 h-2 w-2 shrink-0 rounded-full',
                  item.tone === 'danger'
                    ? 'bg-danger'
                    : item.tone === 'warning'
                      ? 'bg-warning'
                      : 'bg-info',
                ]"
                aria-hidden="true"
              />
              <span class="min-w-0 flex-1">
                <span class="block line-clamp-2 text-sm font-semibold text-fg-strong">{{ item.title }}</span>
                <span
                  v-if="item.detail"
                  class="mt-1 block line-clamp-2 text-xs leading-5 text-fg-muted"
                >
                  {{ item.detail }}
                </span>
                <span class="mt-0.5 block truncate text-xs text-fg-muted">
                  {{ KIND_LABEL[item.kind] }}
                  <template v-if="item.when"> · {{ formatRelativeDateTime(item.when) }}</template>
                </span>
              </span>
              <UiIcon
                name="chevron-right"
                class="mt-1 h-4 w-4 shrink-0 text-fg-subtle"
                aria-hidden="true"
              />
            </button>
          </div>
          <UiEmptyState
            v-else
            title="No items in this group"
            description="Choose another filter to see the remaining attention items."
            size="sm"
          />
        </UiCard>

        <UiCard
          v-if="selectedItem"
          section
          class="h-fit"
        >
          <template #header>
            <div class="flex items-center gap-2">
              <UiIcon
                :name="KIND_ICON[selectedItem.kind]"
                class="h-4 w-4 text-fg-muted"
                aria-hidden="true"
              />
              <UiBadge
                :tone="selectedItem.tone"
                variant="subtle"
              >
                {{ KIND_LABEL[selectedItem.kind] }}
              </UiBadge>
            </div>
            <span
              v-if="selectedItem.when"
              class="text-2xs text-fg-subtle"
              :title="formatAbsoluteDateTime(selectedItem.when)"
            >
              {{ formatRelativeDateTime(selectedItem.when) }}
            </span>
          </template>

          <div class="space-y-5">
            <div>
              <h2 class="t-h2 text-fg-strong">
                {{ selectedItem.title }}
              </h2>
              <p
                v-if="selectedItem.detail"
                class="mt-2 max-w-[72ch] text-sm leading-6 text-fg-default"
              >
                {{ selectedItem.detail }}
              </p>
            </div>

            <div class="grid gap-3 sm:grid-cols-2">
              <section class="rounded-lg border border-subtle bg-bg-surface-alt p-4">
                <p class="t-overline text-fg-subtle">
                  Why it matters
                </p>
                <p class="mt-2 text-sm leading-6 text-fg-default">
                  {{ selectedItem.impact }}
                </p>
              </section>
              <section class="rounded-lg border border-subtle bg-bg-surface-alt p-4">
                <p class="t-overline text-fg-subtle">
                  Who owns what
                </p>
                <p class="mt-2 text-sm leading-6 text-fg-default">
                  {{ selectedItem.ownership }}
                </p>
              </section>
            </div>

            <section class="rounded-lg border border-accent-border bg-accent-subtle p-4">
              <p class="t-overline text-accent-fg">
                After you act
              </p>
              <p class="mt-2 text-sm leading-6 text-fg-default">
                {{ selectedItem.after }}
              </p>
            </section>

            <details class="rounded-lg border border-subtle bg-bg-surface-alt p-4">
              <summary class="focus-ring cursor-pointer rounded-sm text-sm font-medium text-fg-strong">
                Technical context
              </summary>
              <dl class="mt-3 grid gap-3 text-xs sm:grid-cols-2">
                <div>
                  <dt class="text-fg-subtle">
                    Attention ref
                  </dt>
                  <dd class="mt-1 font-mono text-fg-default">
                    {{ selectedItem.id }}
                  </dd>
                </div>
                <div>
                  <dt class="text-fg-subtle">
                    Owning surface
                  </dt>
                  <dd class="mt-1 break-all font-mono text-fg-default">
                    {{ selectedItem.to }}
                  </dd>
                </div>
              </dl>
            </details>

            <div class="flex flex-wrap items-center justify-between gap-3 border-t border-subtle pt-4">
              <UiButton
                variant="ghost"
                @click="openRelatedActivity(selectedItem)"
              >
                Related activity
              </UiButton>
              <UiButton
                icon-right="arrow-right"
                @click="openSelected"
              >
                {{ selectedItem.cta }}
              </UiButton>
            </div>
          </div>
        </UiCard>
      </div>

      <p
        v-if="attention.degraded"
        class="text-2xs text-fg-subtle"
      >
        Some signals could not be loaded. The list may be incomplete.
      </p>
    </template>
  </UiPageShell>
</template>
