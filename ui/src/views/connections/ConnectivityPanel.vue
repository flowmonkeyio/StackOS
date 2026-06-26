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
import { routeNeedsManualProviderUpdate } from './ingressResults'
import type { IngressEndpointRoute, IngressEndpointStatusOut, MessageTone } from './types'

const props = defineProps<{
  ingressStatus: IngressEndpointStatusOut | null
  loading: boolean
  syncing: boolean
  message: { tone: MessageTone; text: string } | null
}>()

defineEmits<{
  (e: 'refresh'): void
  (e: 'configure'): void
  (e: 'sync'): void
}>()

const driverLabel = (): string => {
  const driver = props.ingressStatus?.endpoint?.driver
  if (driver === 'local-tunnel') return 'Local tunnel'
  if (driver === 'public-url') return 'Public URL'
  return driver ?? 'Not configured'
}

function routeKey(route: IngressEndpointRoute): string {
  return `${route.provider_key}:${route.profile_key}`
}

function routeUrl(route: IngressEndpointRoute): string | null {
  return route.next_action?.url ?? route.ingress_url ?? route.local_url ?? null
}

function routeActionUrl(route: IngressEndpointRoute): string | null {
  return route.next_action?.url ?? route.ingress_url ?? null
}

function manualRouteInstruction(route: IngressEndpointRoute): string {
  if (!routeActionUrl(route)) {
    return `Configure a public address before updating ${providerLabel(route.provider_key)} in the provider console.`
  }
  if (route.next_action?.instructions) return route.next_action.instructions
  if (route.provider_key === 'slack-bot') {
    return 'Slack cannot be updated automatically from here. Copy this webhook URL and paste it into the Slack app Event Subscriptions or Interactivity Request URL field.'
  }
  return `Copy this webhook URL and update ${providerLabel(route.provider_key)} in the provider console.`
}

async function copyRouteUrl(route: IngressEndpointRoute): Promise<void> {
  const url = routeActionUrl(route)
  if (!url) return

  try {
    await navigator.clipboard.writeText(url)
  } catch {
    // Clipboard can be unavailable in some browser contexts; keep the URL visible for manual copy.
  }
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
          icon-left="settings"
          @click="$emit('configure')"
        >
          Set up
        </UiButton>
        <UiButton
          v-if="ingressStatus?.endpoint?.public_base_url"
          size="sm"
          variant="secondary"
          icon-left="bolt"
          :loading="syncing"
          @click="$emit('sync')"
        >
          Sync to providers
        </UiButton>
        <UiButton
          size="sm"
          variant="ghost"
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
      messages until a public address is set. Choose <strong>Set up</strong> to add one, then
      <strong>Sync to providers</strong> to register each bot’s webhook.
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
              :key="routeKey(route)"
              class="px-4 py-3"
            >
              <div class="flex min-w-0 flex-wrap items-center justify-between gap-3">
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
                <UiButton
                  v-if="routeNeedsManualProviderUpdate(route) && routeActionUrl(route)"
                  size="sm"
                  variant="secondary"
                  icon-left="copy"
                  :aria-label="`Copy ${providerLabel(route.provider_key)} webhook URL`"
                  @click="copyRouteUrl(route)"
                >
                  {{ route.next_action?.label ?? 'Copy webhook URL' }}
                </UiButton>
              </div>
              <p class="mt-1 break-all font-mono text-2xs text-fg-subtle">
                {{ routeUrl(route) ?? '—' }}
              </p>
              <div
                v-if="routeNeedsManualProviderUpdate(route)"
                class="mt-3 rounded-md border border-subtle bg-bg-surface-alt p-3"
              >
                <p class="text-sm font-medium text-fg-strong">
                  {{ providerLabel(route.provider_key) }} requires manual webhook update.
                </p>
                <p class="mt-1 text-xs leading-5 text-fg-muted">
                  {{ manualRouteInstruction(route) }}
                </p>
              </div>
              <ul
                v-if="route.notes?.length"
                class="mt-2 list-disc space-y-1 pl-5 text-xs text-fg-muted"
              >
                <li
                  v-for="note in route.notes"
                  :key="note"
                >
                  {{ note }}
                </li>
              </ul>
            </li>
          </ul>
        </UiCard>
      </section>
    </template>
  </section>
</template>
