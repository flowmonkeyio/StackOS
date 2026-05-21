<script setup lang="ts">
import { computed } from 'vue'

import type { SchemaContextItemOut, SchemaContextQueryOut } from '@/api'
import { UiBadge, UiJsonBlock, UiPanel, UiSectionHeader } from '@/components/ui'
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
  <UiPanel
    class="p-4"
    :aria-label="title"
  >
    <UiSectionHeader
      :title="title"
      as="h3"
    >
      <template #actions>
        <UiBadge
          v-for="source in sources"
          :key="source"
          tone="info"
        >
          {{ source }}
        </UiBadge>
      </template>
    </UiSectionHeader>

    <p
      v-if="rows.length === 0"
      class="rounded-md border border-dashed border-subtle bg-bg-surface px-4 py-5 text-sm text-fg-muted"
    >
      No context rows.
    </p>
    <ol
      v-else
      class="space-y-2"
    >
      <li
        v-for="item in rows"
        :key="`${item.source}-${item.id}`"
        class="rounded-md border border-subtle bg-bg-surface p-3"
      >
        <div class="mb-2 flex flex-wrap items-center gap-2">
          <UiBadge tone="accent">{{ item.source }}</UiBadge>
          <span class="font-mono text-xs text-fg-muted">#{{ item.id }}</span>
          <span class="min-w-0 flex-1 truncate text-sm font-medium text-fg-default">
            {{ item.title ?? '-' }}
          </span>
          <span class="text-xs text-fg-muted">{{ formatDateTime(item.occurred_at) }}</span>
        </div>
        <UiJsonBlock
          :data="sanitizeForDisplay(item.fields)"
          density="compact"
          max-height="12rem"
          wrap
        />
      </li>
    </ol>
  </UiPanel>
</template>
