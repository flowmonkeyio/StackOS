<script setup lang="ts">
import { computed } from 'vue'
import type { SchemaResourceOut } from '@/api'
import { UiAdvancedJsonPanel, UiFactGroups } from '@/components/ui'
import { artifactReference, sourceFactGroups, type LibrarySource } from '@/lib/librarySources'
import { sanitizeForDisplay } from '@/lib/stackos/json'

const props = defineProps<{ source: LibrarySource; resource?: SchemaResourceOut | null }>()
const groups = computed(() => sourceFactGroups(props.source, props.resource))
const details = computed(() => {
  const { payload, ...identity } = props.source
  if (props.source.source_kind === 'artifact') {
    const artifact = props.source.payload
    if (artifact.plugin_slug === 'finance') {
      return sanitizeForDisplay({ ...identity, payload: {
        id: artifact.id, project_id: artifact.project_id, plugin_slug: artifact.plugin_slug,
        kind: artifact.kind, name: artifact.name, mime_type: artifact.mime_type,
        size_bytes: artifact.size_bytes, status: artifact.status,
        created_at: artifact.created_at, updated_at: artifact.updated_at,
        resource_record_id: artifact.resource_record_id,
        superseded_by_artifact_id: artifact.superseded_by_artifact_id,
        reference: 'Inspect through Finance',
      } })
    }
    // The same classification owns action capability and safe URI display.
    return sanitizeForDisplay({ ...identity, payload: { ...artifact, uri: artifactReference(artifact).display_uri } })
  }
  return sanitizeForDisplay({ ...identity, payload })
})
</script>

<template>
  <div class="min-w-0 space-y-3">
    <UiFactGroups
      v-if="groups.length"
      :groups="groups"
      aria-label="Saved content"
    />
    <p
      v-else
      class="text-sm text-fg-muted"
    >
      No readable field hints are declared. Inspect the saved source in Details.
    </p>
    <UiAdvancedJsonPanel
      title="Details"
      summary="Source and data"
      :data="details"
    />
  </div>
</template>
