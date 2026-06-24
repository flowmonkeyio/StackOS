<script setup lang="ts">
import {
  UiBadge,
  UiButton,
  UiCallout,
  UiCard,
  UiCountBadge,
  UiDescriptionList,
  UiEmptyState,
  UiMedallion,
  UiSectionHeader,
  UiSkeleton,
} from '@/components/ui'
import StatusBadge from '@/components/StatusBadge.vue'

import { providerLabel, routeStatusLabel } from './formatters'
import type { IngressEndpointStatusOut, MessageTone } from './types'

const props = defineProps<{
  ingressStatus: IngressEndpointStatusOut | null
  loading: boolean
  message: { tone: MessageTone; text: string } | null
}>()

defineEmits<{
  (e: 'refresh'): void
}>()

const driverLabel = (): string => {
  const driver = props.ingressStatus?.endpoint?.driver
  if (driver === 'local-tunnel') return 'Local tunnel'
  if (driver === 'public-url') return 'Public URL'
  return driver ?? 'Not configured'
}
</script>

<template>
  <section
    class="space-y-3"
    aria-label="Connectivity"
  >
    <UiSectionHeader
      title="Connectivity"
      description="The public address Slack and Telegram use to reach your bots. Each bot gets its own inbound route."
      as="h3"
    >
      <template #actions>
        <StatusBadge
          domain="system"
          :status="ingressStatus?.ready ? 'ok' : 'degraded'"
          :label="ingressStatus?.ready ? 'Reachable' : 'Not reachable'"
        />
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

    <UiCallout
      v-else-if="!ingressStatus?.ready"
      tone="info"
    >
      Inbound messaging isn’t reachable yet. Bots can still send replies, but they won’t receive new
      messages until a public address is configured. Configuring connectivity is coming to this
      screen — for now it’s managed through StackOS setup.
    </UiCallout>

    <UiCard
      v-if="loading"
      aria-label="Loading connectivity"
    >
      <UiSkeleton
        shape="line"
        :lines="3"
      />
    </UiCard>

    <template v-else>
      <UiCard
        section
        aria-label="Public endpoint"
      >
        <template #header>
          <div class="flex min-w-0 items-center gap-3">
            <UiMedallion
              icon="globe"
              shape="square"
              :tone="ingressStatus?.ready ? 'success' : 'warning'"
            />
            <h4 class="t-h3 text-fg-strong">
              Public endpoint
            </h4>
          </div>
        </template>
        <UiDescriptionList
          :items="[
            { label: 'Status', value: ingressStatus?.endpoint?.status ?? 'Not configured' },
            { label: 'Driver', value: driverLabel() },
            {
              label: 'Public address',
              value: ingressStatus?.endpoint?.public_base_url ?? 'None configured',
              mono: true,
            },
          ]"
        />
      </UiCard>

      <section
        class="space-y-3"
        aria-label="Bot routes"
      >
        <UiSectionHeader
          title="Bot routes"
          as="h4"
        >
          <template #actions>
            <UiCountBadge :value="ingressStatus?.routes?.length ?? 0" />
          </template>
        </UiSectionHeader>

        <UiEmptyState
          v-if="!ingressStatus?.routes?.length"
          size="sm"
          icon="globe"
          title="No routes yet"
          description="Each bot that listens for messages gets its own inbound route here once connectivity is set up."
          framed
        />

        <UiCard
          v-else
          section
          :padded="false"
          class="overflow-hidden"
          aria-label="Route list"
        >
          <ul class="divide-y divide-border-subtle">
            <li
              v-for="route in ingressStatus.routes"
              :key="`${route.provider_key}:${route.profile_key}`"
              class="px-4 py-3"
            >
              <div class="flex min-w-0 flex-wrap items-center gap-2">
                <h5 class="min-w-0 truncate text-sm font-medium text-fg-strong">
                  {{ route.profile_key }}
                </h5>
                <UiBadge tone="accent">
                  {{ providerLabel(route.provider_key) }}
                </UiBadge>
                <UiBadge variant="outline">
                  {{ routeStatusLabel(route) }}
                </UiBadge>
              </div>
              <p class="mt-1 break-all font-mono text-2xs text-fg-subtle">
                {{ route.ingress_url ?? route.local_url ?? '—' }}
              </p>
            </li>
          </ul>
        </UiCard>
      </section>
    </template>
  </section>
</template>
