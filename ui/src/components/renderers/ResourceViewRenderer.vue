<script setup lang="ts">
import { computed } from 'vue'

import type { SchemaResourceOut, SchemaResourceRecordOut } from '@/api'
import { UiBadge, UiJsonBlock, UiPanel, UiSectionHeader } from '@/components/ui'
import { formatDateTime, sanitizeForDisplay } from '@/lib/stackos/json'

const props = defineProps<{
  resource?: SchemaResourceOut | null
  record?: SchemaResourceRecordOut | null
}>()

const title = computed(() => {
  if (props.record) return props.record.title || props.record.external_id || `Record #${props.record.id}`
  if (props.resource) return props.resource.name
  return 'Resource'
})

const isRecordView = computed(() => props.record !== null && props.record !== undefined)
const description = computed(() => (isRecordView.value ? null : props.resource?.description ?? null))
const pluginSlug = computed(() => {
  if (isRecordView.value) return props.record?.plugin_slug ?? null
  return props.resource?.plugin_slug ?? null
})
const resourceKey = computed(() => {
  if (isRecordView.value) return props.record?.resource_key ?? null
  return props.resource?.key ?? null
})
const schema = computed(() => {
  if (isRecordView.value) return sanitizeForDisplay(props.record?.data_json)
  return sanitizeForDisplay(props.resource?.schema_json)
})
const metadata = computed(() => {
  if (isRecordView.value) return sanitizeForDisplay(props.record?.provenance_json)
  return sanitizeForDisplay(props.resource?.config_json)
})
</script>

<template>
  <UiPanel
    class="p-4"
    :aria-label="`${title} resource`"
  >
    <UiSectionHeader
      :title="title"
      :description="description ?? undefined"
      as="h3"
    >
      <template #actions>
        <UiBadge
          v-if="pluginSlug"
          tone="accent"
        >
          {{ pluginSlug }}
        </UiBadge>
        <UiBadge v-if="resourceKey">{{ resourceKey }}</UiBadge>
      </template>
    </UiSectionHeader>

    <dl
      v-if="record"
      class="mb-3 grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-4"
    >
      <div>
        <dt class="text-xs text-fg-muted">External id</dt>
        <dd class="truncate font-mono text-fg-default">{{ record.external_id ?? '-' }}</dd>
      </div>
      <div>
        <dt class="text-xs text-fg-muted">Created</dt>
        <dd>{{ formatDateTime(record.created_at) }}</dd>
      </div>
      <div>
        <dt class="text-xs text-fg-muted">Updated</dt>
        <dd>{{ formatDateTime(record.updated_at) }}</dd>
      </div>
      <div>
        <dt class="text-xs text-fg-muted">Record</dt>
        <dd class="font-mono">#{{ record.id }}</dd>
      </div>
    </dl>

    <UiJsonBlock
      :data="schema"
      density="compact"
      max-height="18rem"
      wrap
    />

    <details
      v-if="metadata"
      class="mt-3 rounded-md border border-subtle bg-bg-surface"
    >
      <summary class="cursor-pointer px-3 py-2 text-sm font-medium text-fg-default focus-ring">
        Metadata
      </summary>
      <div class="border-t border-subtle p-3">
        <UiJsonBlock
          :data="metadata"
          density="compact"
          max-height="14rem"
          wrap
        />
      </div>
    </details>
  </UiPanel>
</template>
