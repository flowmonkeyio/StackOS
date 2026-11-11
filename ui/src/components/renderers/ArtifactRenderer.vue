<script setup lang="ts">
import { computed, onBeforeUpdate, ref } from 'vue'

import type { SchemaArtifactOut } from '@/api'
import { UiBadge, UiButton, UiCard, UiDescriptionList } from '@/components/ui'
import { formatDateTime, sanitizeForDisplay } from '@/lib/stackos/json'
import { artifactReference, toLibrarySource } from '@/lib/librarySources'
import SourceDetailRenderer from './SourceDetailRenderer.vue'

const props = defineProps<{
  artifact: SchemaArtifactOut
  embedded?: boolean
}>()

const pluginSlug = computed(() => props.artifact.plugin_slug)
const source = computed(() => toLibrarySource('artifact', props.artifact))
const reference = computed(() => artifactReference(props.artifact))
const previewing = ref(false)
const previewFailed = ref(false)
const title = computed(() => String(sanitizeForDisplay(props.artifact.name || `Artifact #${props.artifact.id}`)))
const referenceIdentity = () => JSON.stringify([props.artifact.id, props.artifact.project_id, props.artifact.uri])
let renderedReference = referenceIdentity()
onBeforeUpdate(() => {
  const next = referenceIdentity()
  if (next === renderedReference) return
  renderedReference = next
  previewing.value = false
  previewFailed.value = false
})
</script>

<template>
  <UiCard
    section
    :variant="embedded ? 'plain' : 'default'"
    :padded="!embedded"
    :aria-label="`${artifact.kind} artifact`"
  >
    <template #header>
      <div class="flex min-w-0 w-full items-start gap-3">
        <div class="min-w-0 flex-1 space-y-2">
          <div class="min-w-0">
            <h3
              class="break-words text-fg-strong"
              :class="embedded ? 'line-clamp-3 text-xl font-semibold leading-snug' : 't-h3'"
              :title="title"
            >
              {{ title }}
            </h3>
            <p
              v-if="artifact.mime_type"
              class="mt-0.5 font-mono text-2xs text-fg-subtle"
            >
              {{ artifact.mime_type }}
            </p>
          </div>
          <div class="flex min-w-0 max-w-full flex-wrap items-center gap-1.5">
            <UiBadge
              v-if="pluginSlug"
              tone="accent"
              truncate
              :title="pluginSlug"
            >
              {{ pluginSlug }}
            </UiBadge>
            <UiBadge
              truncate
              :title="artifact.kind"
            >
              {{ artifact.kind }}
            </UiBadge>
          </div>
        </div>
        <slot name="actions" />
      </div>
    </template>

    <div :class="embedded ? 'flex flex-col gap-3' : 'space-y-3'">
      <p class="text-xs text-fg-muted">
        {{ reference.reason }}
      </p>
      <div
        v-if="reference.href"
        class="flex flex-wrap items-center gap-3"
        :class="embedded && 'order-2'"
      >
        <a
          :href="reference.href"
          target="_blank"
          rel="noopener noreferrer"
          class="focus-ring text-sm text-fg-link"
        >Open reference</a>
        <UiButton
          v-if="reference.preview && !previewFailed"
          @click="previewing = !previewing"
        >
          {{ previewing ? 'Hide preview' : 'Preview image' }}
        </UiButton>
      </div>
      <img
        v-if="previewing && !previewFailed && reference.href && reference.preview"
        :src="reference.href"
        :alt="title"
        referrerpolicy="no-referrer"
        class="max-h-80 max-w-full rounded-lg object-contain"
        :class="embedded && 'order-1'"
        @error="previewFailed = true"
      >
      <p
        v-if="previewFailed"
        class="text-sm text-fg-muted"
        :class="embedded && 'order-1'"
      >
        Preview unavailable. The saved reference is retained in Details.
      </p>
      <SourceDetailRenderer
        :source="source"
        :reading="embedded"
        :class="embedded && 'order-3'"
      >
        <template
          v-if="embedded"
          #facts
        >
          <UiDescriptionList :items="[{ label: 'Source', value: pluginSlug ?? 'Project' }, { label: 'Added', value: formatDateTime(artifact.created_at) }]" />
        </template>
      </SourceDetailRenderer>
    </div>
  </UiCard>
</template>
