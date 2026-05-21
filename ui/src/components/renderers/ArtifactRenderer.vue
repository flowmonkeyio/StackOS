<script setup lang="ts">
import { computed } from 'vue'

import type { SchemaArtifactOut } from '@/api'
import { UiBadge, UiJsonBlock, UiPanel, UiSectionHeader } from '@/components/ui'
import { formatDateTime, sanitizeForDisplay } from '@/lib/stackos/json'

const props = defineProps<{
  artifact: SchemaArtifactOut
}>()

const metadata = computed(() => sanitizeForDisplay(props.artifact.metadata_json))
const provenance = computed(() => sanitizeForDisplay(props.artifact.provenance_json))
const pluginSlug = computed(() => props.artifact.plugin_slug)
</script>

<template>
  <UiPanel
    class="p-4"
    :aria-label="`${artifact.kind} artifact`"
  >
    <UiSectionHeader
      :title="artifact.name || artifact.uri"
      :description="artifact.mime_type ?? undefined"
      as="h3"
    >
      <template #actions>
        <UiBadge
          v-if="pluginSlug"
          tone="accent"
        >
          {{ pluginSlug }}
        </UiBadge>
        <UiBadge>{{ artifact.kind }}</UiBadge>
      </template>
    </UiSectionHeader>

    <dl class="mb-3 grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-4">
      <div>
        <dt class="text-xs text-fg-muted">URI</dt>
        <dd class="truncate font-mono text-fg-default">{{ artifact.uri }}</dd>
      </div>
      <div>
        <dt class="text-xs text-fg-muted">Size</dt>
        <dd>{{ artifact.size_bytes ?? '-' }}</dd>
      </div>
      <div>
        <dt class="text-xs text-fg-muted">Record</dt>
        <dd>{{ artifact.resource_record_id ? `#${artifact.resource_record_id}` : '-' }}</dd>
      </div>
      <div>
        <dt class="text-xs text-fg-muted">Created</dt>
        <dd>{{ formatDateTime(artifact.created_at) }}</dd>
      </div>
    </dl>

    <div class="grid gap-3 lg:grid-cols-2">
      <div>
        <h4 class="mb-1 text-xs font-semibold uppercase tracking-wide text-fg-muted">
          Metadata
        </h4>
        <UiJsonBlock
          :data="metadata ?? {}"
          density="compact"
          max-height="14rem"
          wrap
        />
      </div>
      <div>
        <h4 class="mb-1 text-xs font-semibold uppercase tracking-wide text-fg-muted">
          Provenance
        </h4>
        <UiJsonBlock
          :data="provenance ?? {}"
          density="compact"
          max-height="14rem"
          wrap
        />
      </div>
    </div>
  </UiPanel>
</template>
