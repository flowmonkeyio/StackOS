<script setup lang="ts">
import type { SchemaFinanceContentOut } from '@/api'
import MarkdownView from '@/components/MarkdownView.vue'
import { UiButton, UiCallout, UiDropdownMenu, UiSectionHeader } from '@/components/ui'

defineProps<{
  report: SchemaFinanceContentOut | null
  reportUrl: string
  reportText: string
  reportError: string
  reportLoading: boolean
  reportAllowed: boolean
}>()
const emit = defineEmits<{ render: [format: 'markdown' | 'csv' | 'json'] }>()
</script>
<template>
  <section class="space-y-3">
    <UiSectionHeader
      title="Reports"
      description="View or export a report for your selected period and filters."
    />
    <div class="flex gap-2">
      <UiButton
        :disabled="!reportAllowed || reportLoading"
        variant="primary"
        @click="emit('render', 'markdown')"
      >
        View report
      </UiButton>
      <UiDropdownMenu
        :items="[
          {
            key: 'csv',
            label: 'Export CSV',
            disabled: !reportAllowed || reportLoading,
            onSelect: () => emit('render', 'csv'),
          },
          {
            key: 'json',
            label: 'Export JSON',
            disabled: !reportAllowed || reportLoading,
            onSelect: () => emit('render', 'json'),
          },
        ]"
        aria-label="Export report"
      >
        <template #trigger="{ toggle }">
          <UiButton
            data-test="export-finance"
            data-dropdown-trigger
            :disabled="!reportAllowed || reportLoading"
            @click="toggle"
          >
            Export
          </UiButton>
        </template>
      </UiDropdownMenu>
    </div>
    <p
      v-if="reportLoading"
      role="status"
    >
      Preparing private report…
    </p>
    <UiCallout
      v-if="reportError"
      tone="danger"
      role="alert"
    >
      {{ reportError }}
    </UiCallout>
    <a
      v-if="reportUrl"
      :href="reportAllowed ? reportUrl : undefined"
      :aria-disabled="!reportAllowed"
      :download="report?.filename || 'finance-report'"
      class="focus-ring text-fg-link"
    >Download {{ report?.filename || 'private report' }}</a>
    <MarkdownView
      v-if="reportText && report?.content_type?.includes('markdown')"
      :source="reportText"
    />
  </section>
</template>
