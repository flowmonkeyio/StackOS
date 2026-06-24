<script setup lang="ts">
import {
  UiBadge,
  UiButton,
  UiCallout,
  UiCard,
  UiCountBadge,
  UiEmptyState,
  UiMedallion,
  UiSectionHeader,
  UiSkeleton,
} from '@/components/ui'
import StatusBadge from '@/components/StatusBadge.vue'

import { providerLabel, targetPolicySummary, targetTitle } from './formatters'
import type { CommunicationTarget, MessageTone } from './types'

defineProps<{
  destinations: CommunicationTarget[]
  loading: boolean
  message: { tone: MessageTone; text: string } | null
}>()

defineEmits<{
  (e: 'refresh'): void
}>()
</script>

<template>
  <section
    class="space-y-3"
    aria-label="Destinations"
  >
    <UiSectionHeader
      title="Destinations"
      description="Approved places agents can send to by name — like ops-alerts — without ever touching raw channel IDs."
      as="h3"
    >
      <template #actions>
        <UiCountBadge :value="destinations.length" />
        <UiButton
          size="sm"
          variant="secondary"
          icon-left="refresh"
          :loading="loading"
          @click="$emit('refresh')"
        >
          Refresh
        </UiButton>
      </template>
    </UiSectionHeader>

    <UiCallout
      v-if="message"
      :tone="message.tone"
    >
      {{ message.text }}
    </UiCallout>

    <UiCard
      v-if="loading"
      aria-label="Loading destinations"
    >
      <UiSkeleton
        shape="line"
        :lines="3"
      />
    </UiCard>

    <UiEmptyState
      v-else-if="destinations.length === 0"
      title="No destinations yet"
      description="Destinations give agents a safe vocabulary for where they can send — for example a named internal Slack channel — with policy guards on who can use them."
      icon="arrow-right"
      framed
    />

    <UiCard
      v-else
      section
      :padded="false"
      class="overflow-hidden"
      aria-label="Destination list"
    >
      <ul class="divide-y divide-border-subtle">
        <li
          v-for="destination in destinations"
          :key="destination.target_ref"
          class="px-4 py-3"
        >
          <div class="flex min-w-0 items-start gap-3">
            <UiMedallion
              icon="arrow-right"
              tone="info"
              class="mt-0.5 shrink-0"
            />
            <div class="min-w-0 flex-1">
              <div class="flex min-w-0 flex-wrap items-center gap-2">
                <h4 class="min-w-0 truncate text-sm font-medium text-fg-strong">
                  {{ targetTitle(destination) }}
                </h4>
                <UiBadge tone="accent">
                  {{ providerLabel(destination.provider_key) }}
                </UiBadge>
                <StatusBadge
                  domain="step"
                  :status="destination.enabled ? 'enabled' : 'disabled'"
                />
                <UiBadge variant="outline">
                  {{ targetPolicySummary(destination) }}
                </UiBadge>
              </div>
              <p class="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-fg-muted">
                <span class="font-mono text-2xs text-fg-subtle">{{ destination.key }}</span>
                <span aria-hidden="true">→</span>
                <span class="truncate font-mono text-2xs text-fg-subtle">
                  {{ destination.surface_ref }}
                </span>
              </p>
              <p class="mt-1 truncate text-xs text-fg-subtle">
                {{ destination.action_ref || 'No action bound yet' }}
              </p>
            </div>
          </div>
        </li>
      </ul>
    </UiCard>
  </section>
</template>
