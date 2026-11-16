<script setup lang="ts">
import { ref } from 'vue'
import type { SchemaTaskActivityItem } from '@/api'
import { UiButton, UiCallout, UiJsonBlock } from '@/components/ui'
import { formatDateTime, sanitizeForDisplay } from '@/lib/stackos/json'
import type { TaskInspectionPageState } from './useTaskInspectionPage'

defineProps<{ state?: TaskInspectionPageState<SchemaTaskActivityItem> }>()
defineEmits<{ more: []; retry: [] }>()
const expanded = ref(false)
</script>

<template>
  <section
    class="space-y-2 border-t border-subtle pt-3"
    aria-label="Task history"
  >
    <h3 class="t-h3 text-fg-strong">
      History
    </h3>
    <UiCallout
      v-if="state?.error"
      tone="warning"
      density="compact"
    >
      {{ state.error }}
      <UiButton
        size="sm"
        :loading="state.loading"
        @click="$emit('retry')"
      >
        Retry history
      </UiButton>
    </UiCallout>
    <ol
      v-if="state?.items.length"
      class="divide-y divide-subtle"
    >
      <li
        v-for="item in state.items"
        :key="`${item.source_kind}:${item.source_id}`"
      >
        <details class="py-1.5 text-sm">
          <summary class="focus-ring flex cursor-pointer flex-wrap gap-x-4 gap-y-1 rounded-xs">
            <time
              :datetime="item.occurred_at"
              class="text-xs text-fg-muted"
            >{{ formatDateTime(item.occurred_at) }}</time>
            <span class="min-w-0 flex-1 break-words text-fg-default">{{ item.summary }}</span>
          </summary>
          <div class="mt-2 space-y-2 text-xs text-fg-muted">
            <p>{{ item.kind }}<span v-if="item.phase"> · {{ item.phase }}</span><span v-if="item.ticket_key"> · {{ item.ticket_key }}</span></p>
            <p>Source: {{ item.source_kind }} #{{ item.source_id }}</p>
            <UiJsonBlock
              :data="sanitizeForDisplay(item.details)"
              density="compact"
              max-height="12rem"
              wrap
            />
          </div>
        </details>
      </li>
    </ol>
    <p
      v-else-if="state?.loading"
      role="status"
      class="text-sm text-fg-muted"
    >
      Loading history…
    </p>
    <p
      v-else-if="state?.loaded && !state.error"
      class="text-sm text-fg-muted"
    >
      No recorded activity is linked to this task.
    </p>
    <UiButton
      v-if="state?.nextCursor"
      variant="ghost"
      size="sm"
      :loading="state.loading"
      @click="expanded = true; $emit('more')"
    >
      {{ expanded ? 'Load older history' : 'View full history' }}
    </UiButton>
    <p
      v-for="warning in state?.warnings ?? []"
      :key="warning"
      class="text-xs text-warning-fg"
    >
      {{ warning }}
    </p>
    <details
      v-if="state?.coverage"
      class="text-xs text-fg-muted"
    >
      <summary class="focus-ring cursor-pointer">
        History sources
      </summary>
      <p class="mt-2">
        {{ state.coverage }}
      </p>
    </details>
  </section>
</template>
