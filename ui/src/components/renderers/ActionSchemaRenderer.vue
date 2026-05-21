<script setup lang="ts">
import { computed } from 'vue'

import type { SchemaActionOut } from '@/api'
import { UiBadge, UiJsonBlock, UiPanel, UiSectionHeader } from '@/components/ui'
import { sanitizeForDisplay } from '@/lib/stackos/json'

const props = defineProps<{
  action: SchemaActionOut
}>()

const inputSchema = computed(() => sanitizeForDisplay(props.action.input_schema_json))
const outputSchema = computed(() => sanitizeForDisplay(props.action.output_schema_json))
const config = computed(() => sanitizeForDisplay(props.action.config_json))
</script>

<template>
  <UiPanel
    class="p-4"
    :aria-label="`${action.name} action schema`"
  >
    <UiSectionHeader
      :title="action.name"
      :description="action.description"
      as="h3"
    >
      <template #actions>
        <UiBadge tone="accent">{{ action.plugin_slug }}</UiBadge>
        <UiBadge>{{ action.key }}</UiBadge>
        <UiBadge
          v-if="action.provider_key"
          tone="info"
        >
          {{ action.provider_key }}
        </UiBadge>
        <UiBadge
          :tone="action.risk_level === 'read' ? 'success' : 'warning'"
        >
          {{ action.risk_level }}
        </UiBadge>
      </template>
    </UiSectionHeader>

    <div class="grid gap-3 lg:grid-cols-2">
      <div>
        <h4 class="mb-1 text-xs font-semibold uppercase tracking-wide text-fg-muted">
          Input
        </h4>
        <UiJsonBlock
          :data="inputSchema"
          density="compact"
          max-height="18rem"
          wrap
        />
      </div>
      <div>
        <h4 class="mb-1 text-xs font-semibold uppercase tracking-wide text-fg-muted">
          Output
        </h4>
        <UiJsonBlock
          :data="outputSchema"
          density="compact"
          max-height="18rem"
          wrap
        />
      </div>
    </div>

    <details
      v-if="config"
      class="mt-3 rounded-md border border-subtle bg-bg-surface"
    >
      <summary class="cursor-pointer px-3 py-2 text-sm font-medium text-fg-default focus-ring">
        Connector config
      </summary>
      <div class="border-t border-subtle p-3">
        <UiJsonBlock
          :data="config"
          density="compact"
          max-height="14rem"
          wrap
        />
      </div>
    </details>
  </UiPanel>
</template>
