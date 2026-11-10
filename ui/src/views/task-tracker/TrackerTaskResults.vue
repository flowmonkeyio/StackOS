<script setup lang="ts">
import type { SchemaTaskResultItem } from '@/api'
import { UiBadge, UiButton, UiCallout, UiIcon } from '@/components/ui'
import { toLibrarySource, type LibraryPayloads, type LibrarySource } from '@/lib/librarySources'
import { humanLabel, sourceType } from '@/views/library/viewModel'
import type { TaskInspectionPageState } from './useTaskInspectionPage'

defineProps<{ state?: TaskInspectionPageState<SchemaTaskResultItem> }>()
defineEmits<{ open: [source: LibrarySource]; more: []; retry: [] }>()
function source(item: SchemaTaskResultItem): LibrarySource {
  return toLibrarySource(item.source_kind, item.data as LibraryPayloads[typeof item.source_kind])
}
</script>

<template>
  <section
    class="space-y-2 border-t border-subtle pt-3"
    aria-label="Task results"
  >
    <h3 class="t-h3 text-fg-strong">
      Results
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
        Retry results
      </UiButton>
    </UiCallout>
    <div
      v-if="state?.items.length"
      class="overflow-hidden rounded-lg border border-subtle"
    >
      <button
        v-for="item in state.items"
        :key="`${item.source_kind}:${item.source_id}`"
        type="button"
        class="focus-ring flex min-h-8 w-full items-center gap-3 border-b border-subtle px-3 py-1.5 text-left text-sm last:border-b-0 hover:bg-bg-surface-alt"
        :data-result-source="`${item.source_kind}:${item.source_id}`"
        @click="$emit('open', source(item))"
      >
        <span
          class="min-w-0 flex-1 truncate text-fg-default"
          :title="source(item).title"
        >{{ source(item).title }}</span>
        <UiBadge
          class="max-w-[40%] truncate"
          :title="humanLabel(sourceType(source(item)))"
        >
          {{ humanLabel(sourceType(source(item))) }}
        </UiBadge>
        <UiIcon
          name="chevron-right"
          class="h-4 w-4 shrink-0 text-fg-muted"
        />
      </button>
    </div>
    <p
      v-else-if="state?.loading"
      role="status"
      class="text-sm text-fg-muted"
    >
      Loading results…
    </p>
    <p
      v-else-if="state?.loaded && !state.error"
      class="text-sm text-fg-muted"
    >
      No saved results are explicitly linked to this task.
    </p>
    <UiButton
      v-if="state?.nextCursor !== null && state?.nextCursor"
      size="sm"
      :loading="state.loading"
      @click="$emit('more')"
    >
      Load more results
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
        Result sources
      </summary>
      <p class="mt-2">
        {{ state.coverage }}
      </p>
    </details>
  </section>
</template>
