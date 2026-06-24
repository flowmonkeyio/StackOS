<script setup lang="ts">
import { computed } from 'vue'

import type { SchemaContextItemOut, SchemaContextQueryOut } from '@/api'
import { UiBadge, UiCard, UiCountBadge, UiJsonSection } from '@/components/ui'
import { formatDateTime, sanitizeForDisplay } from '@/lib/stackos/json'

const props = withDefaults(defineProps<{
  title?: string
  query?: SchemaContextQueryOut | null
  items?: SchemaContextItemOut[]
}>(), {
  title: 'Context',
  query: null,
  items: () => [],
})

const rows = computed(() => props.query?.items ?? props.items)
const sources = computed(() => props.query?.sources ?? Array.from(new Set(rows.value.map((r) => r.source))))
</script>

<template>
  <UiCard
    section
    :padded="false"
    class="overflow-hidden"
    :aria-label="title"
  >
    <template #header>
      <div class="flex min-w-0 items-center gap-2">
        <h3 class="t-h3 text-fg-strong">
          {{ title }}
        </h3>
        <UiCountBadge :value="rows.length" />
      </div>
      <div
        v-if="sources.length"
        class="flex shrink-0 flex-wrap items-center justify-end gap-1.5"
      >
        <UiBadge
          v-for="source in sources"
          :key="source"
          tone="info"
        >
          {{ source }}
        </UiBadge>
      </div>
    </template>

    <p
      v-if="rows.length === 0"
      class="px-4 py-3 text-sm text-fg-muted"
    >
      No context rows for this query.
    </p>
    <ol
      v-else
      class="divide-y divide-border-subtle"
    >
      <li
        v-for="item in rows"
        :key="`${item.source}-${item.id}`"
        class="space-y-2 px-4 py-3"
      >
        <div class="flex flex-wrap items-center gap-2">
          <UiBadge tone="accent">
            {{ item.source }}
          </UiBadge>
          <span class="font-mono text-2xs text-fg-subtle">#{{ item.id }}</span>
          <span class="min-w-0 flex-1 truncate text-sm font-medium text-fg-default">
            {{ item.title ?? '—' }}
          </span>
          <span class="shrink-0 text-2xs text-fg-subtle">
            {{ formatDateTime(item.occurred_at) }}
          </span>
        </div>
        <UiJsonSection
          title="Fields"
          :data="sanitizeForDisplay(item.fields)"
          max-height="12rem"
        />
      </li>
    </ol>
  </UiCard>
</template>
