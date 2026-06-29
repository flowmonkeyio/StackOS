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

import {
  channelKindLabel,
  providerLabel,
  sensitivityMeta,
  surfaceAudienceLabel,
  surfaceIntentSummary,
  surfaceTitle,
} from './formatters'
import type { CommunicationSurface, MessageTone } from './types'

defineProps<{
  channels: CommunicationSurface[]
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
    aria-label="Channels"
  >
    <UiSectionHeader
      title="Channels"
      description="The places agents can read or post — a Slack channel, a Telegram group, a mailbox. Sensitivity tells agents what is safe to share."
      as="h3"
    >
      <template #actions>
        <UiCountBadge :value="channels.length" />
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
      aria-label="Loading channels"
    >
      <UiSkeleton
        shape="line"
        :lines="3"
      />
    </UiCard>

    <UiEmptyState
      v-else-if="channels.length === 0"
      title="No channels yet"
      description="Channels appear here once a bot sees a conversation, or when one is registered through StackOS. Each one carries audience and sensitivity so agents know where they are acting."
      icon="megaphone"
      framed
    />

    <UiCard
      v-else
      section
      :padded="false"
      class="overflow-hidden"
      aria-label="Channel list"
    >
      <ul class="divide-y divide-border-subtle">
        <li
          v-for="channel in channels"
          :key="channel.surface_ref"
          class="px-4 py-3"
        >
          <div class="flex min-w-0 items-start gap-3">
            <UiMedallion
              icon="megaphone"
              tone="info"
              class="mt-0.5 shrink-0"
            />
            <div class="min-w-0 flex-1">
              <div class="flex min-w-0 flex-wrap items-center gap-2">
                <h4 class="min-w-0 truncate text-sm font-medium text-fg-strong">
                  {{ surfaceTitle(channel) }}
                </h4>
                <UiBadge tone="accent">
                  {{ providerLabel(channel.provider_key) }}
                </UiBadge>
                <UiBadge variant="outline">
                  {{ channelKindLabel(channel) }}
                </UiBadge>
                <UiBadge variant="outline">
                  {{ surfaceAudienceLabel(channel) }}
                </UiBadge>
                <UiBadge :tone="sensitivityMeta(channel).tone">
                  {{ sensitivityMeta(channel).label }}
                </UiBadge>
                <StatusBadge
                  domain="step"
                  :status="channel.send_enabled ? 'enabled' : 'disabled'"
                  :label="channel.send_enabled ? 'Send on' : 'Send off'"
                />
              </div>
              <p class="mt-1 line-clamp-2 text-xs text-fg-muted">
                {{ surfaceIntentSummary(channel) }}
              </p>
              <p class="mt-1 text-xs text-fg-subtle">
                {{ sensitivityMeta(channel).hint }}
              </p>
              <p class="mt-1 truncate font-mono text-2xs text-fg-subtle">
                {{ channel.surface_ref }}
              </p>
            </div>
          </div>
        </li>
      </ul>
    </UiCard>
  </section>
</template>
