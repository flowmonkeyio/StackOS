<script setup lang="ts">
import { computed } from 'vue'

import type { SchemaArtifactOut } from '@/api'
import { UiAdvancedJsonPanel, UiBadge, UiCard, UiDescriptionList } from '@/components/ui'
import type { DLItem } from '@/components/ui/UiDescriptionList.vue'
import { formatDateTime, sanitizeForDisplay } from '@/lib/stackos/json'

const props = defineProps<{
  artifact: SchemaArtifactOut
}>()

const metadata = computed(() => sanitizeForDisplay(props.artifact.metadata_json))
const provenance = computed(() => sanitizeForDisplay(props.artifact.provenance_json))
const pluginSlug = computed(() => props.artifact.plugin_slug)

const facts = computed<DLItem[]>(() => [
  { label: 'URI', value: props.artifact.uri, mono: true },
  { label: 'Size', value: props.artifact.size_bytes ?? null },
  {
    label: 'Record',
    value: props.artifact.resource_record_id ? `#${props.artifact.resource_record_id}` : null,
  },
  { label: 'Created', value: formatDateTime(props.artifact.created_at) },
])
</script>

<template>
  <UiCard
    section
    :aria-label="`${artifact.kind} artifact`"
  >
    <template #header>
      <div class="min-w-0">
        <h3
          class="t-h3 truncate text-fg-strong"
          :title="artifact.name || artifact.uri"
        >
          {{ artifact.name || artifact.uri }}
        </h3>
        <p
          v-if="artifact.mime_type"
          class="mt-0.5 font-mono text-2xs text-fg-subtle"
        >
          {{ artifact.mime_type }}
        </p>
      </div>
      <div class="flex shrink-0 items-center gap-1.5">
        <UiBadge
          v-if="pluginSlug"
          tone="accent"
        >
          {{ pluginSlug }}
        </UiBadge>
        <UiBadge>{{ artifact.kind }}</UiBadge>
      </div>
    </template>

    <div class="space-y-3">
      <UiDescriptionList
        layout="grid"
        :columns="4"
        :items="facts"
        aria-label="Artifact facts"
      />

      <UiAdvancedJsonPanel
        title="Metadata"
        summary="Raw JSON"
        :data="metadata ?? {}"
        max-height="14rem"
      />
      <UiAdvancedJsonPanel
        title="Provenance"
        summary="Raw JSON"
        :data="provenance ?? {}"
        max-height="14rem"
      />
    </div>
  </UiCard>
</template>
