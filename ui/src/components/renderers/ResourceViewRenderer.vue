<script setup lang="ts">
import { computed } from 'vue'

import type { SchemaResourceOut, SchemaResourceRecordOut } from '@/api'
import { UiAdvancedJsonPanel, UiBadge, UiCard, UiDescriptionList, UiJsonSection } from '@/components/ui'
import type { DLItem } from '@/components/ui/UiDescriptionList.vue'
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
const schemaTitle = computed(() => (isRecordView.value ? 'Data' : 'Schema'))
const schema = computed(() => {
  if (isRecordView.value) return sanitizeForDisplay(props.record?.data_json)
  return sanitizeForDisplay(props.resource?.schema_json)
})
const metadata = computed(() => {
  if (isRecordView.value) return sanitizeForDisplay(props.record?.provenance_json)
  return sanitizeForDisplay(props.resource?.config_json)
})

const recordFacts = computed<DLItem[]>(() => {
  if (!props.record) return []
  return [
    { label: 'External ID', value: props.record.external_id, mono: true },
    { label: 'Record', value: `#${props.record.id}`, mono: true },
    { label: 'Created', value: formatDateTime(props.record.created_at) },
    { label: 'Updated', value: formatDateTime(props.record.updated_at) },
  ]
})
</script>

<template>
  <UiCard
    section
    :aria-label="`${title} resource`"
  >
    <template #header>
      <div class="min-w-0">
        <h3
          class="t-h3 truncate text-fg-strong"
          :title="title"
        >
          {{ title }}
        </h3>
        <p
          v-if="description"
          class="mt-0.5 text-xs text-fg-muted"
        >
          {{ description }}
        </p>
      </div>
      <div class="flex shrink-0 items-center gap-1.5">
        <UiBadge
          v-if="pluginSlug"
          tone="accent"
        >
          {{ pluginSlug }}
        </UiBadge>
        <UiBadge v-if="resourceKey">
          {{ resourceKey }}
        </UiBadge>
      </div>
    </template>

    <div class="space-y-3">
      <UiDescriptionList
        v-if="record"
        layout="grid"
        :columns="4"
        :items="recordFacts"
        aria-label="Record facts"
      />

      <UiJsonSection
        :title="schemaTitle"
        :data="schema"
        max-height="18rem"
      />

      <UiAdvancedJsonPanel
        v-if="metadata"
        title="Metadata"
        summary="Raw JSON"
        :data="metadata"
        max-height="14rem"
      />
    </div>
  </UiCard>
</template>
