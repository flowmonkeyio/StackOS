<script setup lang="ts">
// InboxView — the human attention surface. Everything waiting on a person,
// grouped and ranked: questions, blocked work, failed jobs, connection
// problems, budget alerts. Reads the shared attention aggregator; each row
// routes to where the action happens. First-load skeletons only; background
// polls update in place without flicker.

import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'

import { AttentionItemRow, ProjectPageHeader } from '@/components/domain'
import { UiButton, UiCard, UiCountBadge, UiEmptyState, UiPageShell, UiSkeleton } from '@/components/ui'
import { usePolling } from '@/composables/usePolling'
import { formatAbsoluteDateTime, formatRelativeDateTime } from '@/lib/stackos/time'
import { useAttentionStore, type AttentionItem, type AttentionKind } from '@/stores/attention'

const route = useRoute()
const projectId = computed(() => Number.parseInt(route.params.id as string, 10))

const attention = useAttentionStore()
const loaded = ref(false)

const GROUPS: { kind: AttentionKind; label: string }[] = [
  { kind: 'question', label: 'Questions' },
  { kind: 'blocked', label: 'Blocked work' },
  { kind: 'failed-run', label: 'Failed jobs' },
  { kind: 'connection', label: 'Connections' },
  { kind: 'budget', label: 'Spend' },
]

const grouped = computed(() =>
  GROUPS.map((group) => ({
    ...group,
    items: attention.items.filter((item: AttentionItem) => item.kind === group.kind),
  })).filter((group) => group.items.length > 0),
)

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
      title="Inbox"
      description="Approvals, questions, and everything waiting on you."
      :breadcrumbs="[{ label: 'Inbox' }]"
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
      class="space-y-3"
    >
      <UiSkeleton
        v-for="n in 4"
        :key="n"
        shape="block"
        height="3.5rem"
      />
    </div>

    <UiCard
      v-else-if="attention.items.length === 0"
      section
    >
      <UiEmptyState
        icon="check-circle"
        title="You’re all caught up"
        description="When agents need an approval, ask a question, hit a blocker, or a job fails, it shows up here."
      />
    </UiCard>

    <div
      v-else
      class="space-y-5"
    >
      <UiCard
        v-for="group in grouped"
        :key="group.kind"
        section
        :padded="false"
        class="overflow-hidden"
      >
        <template #header>
          <div class="flex items-center gap-2">
            <h2 class="t-h3 text-fg-strong">
              {{ group.label }}
            </h2>
            <UiCountBadge :value="group.items.length" />
          </div>
        </template>
        <div class="divide-y divide-border-subtle">
          <AttentionItemRow
            v-for="item in group.items"
            :key="item.id"
            :item="item"
          />
        </div>
      </UiCard>

      <p
        v-if="attention.degraded"
        class="text-2xs text-fg-subtle"
      >
        Some signals couldn’t be loaded — this list may be incomplete.
      </p>
    </div>
  </UiPageShell>
</template>
