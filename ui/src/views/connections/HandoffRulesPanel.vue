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

import { routeFieldSummary, routePurpose } from './formatters'
import type { CommunicationRoute, MessageTone } from './types'

defineProps<{
  routes: CommunicationRoute[]
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
    aria-label="Handoff rules"
  >
    <UiSectionHeader
      title="Handoff rules"
      description="When agents may forward a message from one channel to another — what can move, and what needs approval."
      as="h3"
    >
      <template #actions>
        <UiCountBadge :value="routes.length" />
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
      aria-label="Loading handoff rules"
    >
      <UiSkeleton
        shape="line"
        :lines="3"
      />
    </UiCard>

    <UiEmptyState
      v-else-if="routes.length === 0"
      title="No handoff rules"
      description="Handoff rules let agents move a message from one channel to another under explicit policy. They’re registered through StackOS as agents set up cross-channel work."
      icon="git-branch"
      framed
    />

    <UiCard
      v-else
      section
      :padded="false"
      class="overflow-hidden"
      aria-label="Handoff rule list"
    >
      <ul class="divide-y divide-border-subtle">
        <li
          v-for="route in routes"
          :key="route.route_ref"
          class="px-4 py-3"
        >
          <div class="flex min-w-0 items-start gap-3">
            <UiMedallion
              icon="git-branch"
              tone="info"
              class="mt-0.5 shrink-0"
            />
            <div class="min-w-0 flex-1">
              <div class="flex min-w-0 flex-wrap items-center gap-2">
                <h4 class="min-w-0 truncate text-sm font-medium text-fg-strong">
                  {{ route.key }}
                </h4>
                <StatusBadge
                  domain="step"
                  :status="route.enabled ? 'enabled' : 'disabled'"
                />
                <UiBadge
                  v-if="route.requires_approval"
                  tone="warning"
                >
                  Approval required
                </UiBadge>
                <UiBadge variant="outline">
                  {{ routeFieldSummary(route) }}
                </UiBadge>
              </div>
              <p
                v-if="routePurpose(route)"
                class="mt-1 line-clamp-2 text-xs text-fg-muted"
              >
                {{ routePurpose(route) }}
              </p>
              <p class="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-fg-subtle">
                <span class="truncate font-mono text-2xs">
                  {{ route.source_surface_refs.join(', ') || '—' }}
                </span>
                <span aria-hidden="true">→</span>
                <span class="truncate font-mono text-2xs">
                  {{ route.target_refs.join(', ') || '—' }}
                </span>
              </p>
            </div>
          </div>
        </li>
      </ul>
    </UiCard>
  </section>
</template>
