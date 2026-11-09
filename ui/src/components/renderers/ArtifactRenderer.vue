<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { SchemaArtifactOut } from '@/api'
import { UiBadge, UiButton, UiCard } from '@/components/ui'
import { sanitizeForDisplay } from '@/lib/stackos/json'
import { artifactReference, toLibrarySource } from '@/lib/librarySources'
import SourceDetailRenderer from './SourceDetailRenderer.vue'

const props = defineProps<{
  artifact: SchemaArtifactOut
}>()

const pluginSlug = computed(() => props.artifact.plugin_slug)
const source = computed(() => toLibrarySource('artifact', props.artifact))
const reference = computed(() => artifactReference(props.artifact))
const previewing = ref(false)
const previewFailed = ref(false)
const title = computed(() => String(sanitizeForDisplay(props.artifact.name || `Artifact #${props.artifact.id}`)))
watch(() => [props.artifact.id, props.artifact.project_id, props.artifact.uri], () => {
  previewing.value = false
  previewFailed.value = false
})
</script>

<template>
  <UiCard
    section
    :aria-label="`${artifact.kind} artifact`"
  >
    <template #header>
      <div class="min-w-0 w-full space-y-2">
        <div class="min-w-0">
          <h3
            class="t-h3 break-words text-fg-strong"
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
        <div class="flex flex-wrap items-center gap-1.5">
          <UiBadge
            v-if="pluginSlug"
            tone="accent"
          >
            {{ pluginSlug }}
          </UiBadge>
          <UiBadge>{{ artifact.kind }}</UiBadge>
        </div>
      </div>
    </template>

    <div class="space-y-3">
      <p class="text-xs text-fg-muted">
        {{ reference.reason }}
      </p>
      <div
        v-if="reference.href"
        class="flex flex-wrap items-center gap-3"
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
        @error="previewFailed = true"
      >
      <p
        v-if="previewFailed"
        class="text-sm text-fg-muted"
      >
        Preview unavailable. The saved reference is retained in Details.
      </p>
      <SourceDetailRenderer :source="source" />
    </div>
  </UiCard>
</template>
