<script setup lang="ts">
import { computed } from 'vue'

import {
  UiButton,
  UiCallout,
  UiCard,
  UiCountBadge,
  UiMedallion,
  UiMetricCard,
  UiSectionHeader,
  UiSkeleton,
} from '@/components/ui'
import { connectionAttentionTone, connectionTitle } from './formatters'
import type {
  CommunicationProfile,
  CommunicationSurface,
  CommunicationTarget,
  ConnectionRow,
  ConnectionSection,
  IngressEndpointStatusOut,
} from './types'

const props = defineProps<{
  loading: boolean
  connectedServiceCount: number
  activeConnectionsCount: number
  attentionConnections: ConnectionRow[]
  serviceGroupsCount: number
  bots: CommunicationProfile[]
  channels: CommunicationSurface[]
  destinations: CommunicationTarget[]
  ingressStatus: IngressEndpointStatusOut | null
}>()

const emit = defineEmits<{
  (e: 'navigate', section: ConnectionSection): void
  (e: 'add-connection'): void
  (e: 'add-bot'): void
}>()

interface AttentionItem {
  id: string
  tone: 'danger' | 'warning' | 'info'
  icon: string
  title: string
  detail: string
  actionLabel: string
  section: ConnectionSection
}

const liveBots = computed(() => props.bots.filter((bot) => bot.enabled))
const ingressReady = computed(() => Boolean(props.ingressStatus?.ready))

const isEmpty = computed(
  () =>
    props.activeConnectionsCount === 0 && props.bots.length === 0 && props.channels.length === 0,
)

const attentionItems = computed<AttentionItem[]>(() => {
  const items: AttentionItem[] = []

  for (const connection of props.attentionConnections) {
    const tone = connectionAttentionTone(connection)
    items.push({
      id: `connection:${connection.credential_ref}`,
      tone,
      icon: tone === 'danger' ? 'alert-octagon' : 'alert-triangle',
      title: `${connectionTitle(connection)} needs attention`,
      detail: tone === 'danger'
        ? 'This connection failed its last check. Re-test it or re-enter the credential.'
        : 'Setup is incomplete for this connection. Finish or re-test it.',
      actionLabel: 'Review',
      section: 'services',
    })
  }

  for (const bot of liveBots.value) {
    const allowedUsers = bot.access_policy.allowed_user_refs?.length ?? 0
    if (allowedUsers === 0) {
      items.push({
        id: `bot:${bot.profile_ref}`,
        tone: 'warning',
        icon: 'users',
        title: `${bot.identity.display_name || bot.key} has no allowed users`,
        detail: 'Add at least one allowed user before this bot can trigger agents.',
        actionLabel: 'Configure bot',
        section: 'bots',
      })
    }
  }

  if (liveBots.value.length > 0 && !ingressReady.value) {
    items.push({
      id: 'ingress',
      tone: 'warning',
      icon: 'globe',
      title: 'Inbound messaging isn’t reachable yet',
      detail: 'Set up connectivity so Slack and Telegram can deliver messages to your bots.',
      actionLabel: 'Set up connectivity',
      section: 'connectivity',
    })
  }

  return items
})
</script>

<template>
  <section
    class="space-y-5"
    aria-label="Connections overview"
  >
    <UiCard
      v-if="loading"
      role="status"
      aria-label="Loading overview"
    >
      <UiSkeleton
        shape="line"
        :lines="3"
      />
    </UiCard>

    <template v-else>
      <UiCallout
        v-if="isEmpty"
        tone="info"
        title="Let’s connect your first integration"
      >
        StackOS keeps every secret on this machine. Agents only ever receive a safe reference —
        never your API keys or bot tokens. Start by connecting a tool your agents should use, or a
        messaging channel they should answer on.
      </UiCallout>

      <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <UiMetricCard
          label="Services connected"
          :value="connectedServiceCount"
          icon="plug"
          density="compact"
        />
        <UiMetricCard
          label="Bots configured"
          :value="bots.length"
          icon="chat"
          density="compact"
        />
        <UiMetricCard
          label="Channels"
          :value="channels.length"
          icon="megaphone"
          density="compact"
        />
        <UiMetricCard
          label="Needs attention"
          :value="attentionItems.length"
          icon="alert-triangle"
          density="compact"
          :value-tone="attentionItems.length > 0 ? 'danger' : 'default'"
        />
      </div>

      <section
        class="space-y-3"
        aria-label="Needs attention"
      >
        <UiSectionHeader
          title="Needs attention"
          as="h3"
        >
          <template #actions>
            <UiCountBadge
              :value="attentionItems.length"
              :tone="attentionItems.length > 0 ? 'danger' : 'neutral'"
            />
          </template>
        </UiSectionHeader>

        <UiCallout
          v-if="attentionItems.length === 0"
          tone="success"
        >
          Everything looks healthy. Connections are live and bots are reachable.
        </UiCallout>

        <UiCard
          v-else
          section
          :padded="false"
          class="overflow-hidden"
          aria-label="Attention items"
        >
          <ul class="divide-y divide-border-subtle">
            <li
              v-for="item in attentionItems"
              :key="item.id"
              class="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center"
            >
              <UiMedallion
                :icon="item.icon"
                :tone="item.tone"
                class="shrink-0"
              />
              <div class="min-w-0 flex-1">
                <p class="text-sm font-medium text-fg-strong">
                  {{ item.title }}
                </p>
                <p class="mt-0.5 text-xs text-fg-muted">
                  {{ item.detail }}
                </p>
              </div>
              <UiButton
                size="sm"
                variant="secondary"
                class="shrink-0"
                @click="emit('navigate', item.section)"
              >
                {{ item.actionLabel }}
              </UiButton>
            </li>
          </ul>
        </UiCard>
      </section>

      <section
        class="space-y-3"
        aria-label="Quick actions"
      >
        <UiSectionHeader
          title="Get things done"
          as="h3"
        />
        <div class="grid gap-3 sm:grid-cols-3">
          <button
            type="button"
            class="focus-ring group flex items-start gap-3 rounded-lg border border-subtle bg-bg-surface p-4 text-left transition-colors duration-fast hover:border-strong"
            @click="emit('add-connection')"
          >
            <UiMedallion
              icon="plug"
              shape="square"
              tone="info"
              class="shrink-0"
            />
            <span class="min-w-0">
              <span class="block text-sm font-medium text-fg-strong">Connect a tool</span>
              <span class="mt-0.5 block text-xs text-fg-muted">
                Add an API key or account so agents can use a provider.
              </span>
            </span>
          </button>

          <button
            type="button"
            class="focus-ring group flex items-start gap-3 rounded-lg border border-subtle bg-bg-surface p-4 text-left transition-colors duration-fast hover:border-strong"
            @click="emit('add-bot')"
          >
            <UiMedallion
              icon="chat"
              shape="square"
              tone="info"
              class="shrink-0"
            />
            <span class="min-w-0">
              <span class="block text-sm font-medium text-fg-strong">Set up a bot</span>
              <span class="mt-0.5 block text-xs text-fg-muted">
                Give agents a messaging identity on Slack or Telegram.
              </span>
            </span>
          </button>

          <button
            type="button"
            class="focus-ring group flex items-start gap-3 rounded-lg border border-subtle bg-bg-surface p-4 text-left transition-colors duration-fast hover:border-strong"
            @click="emit('navigate', 'connectivity')"
          >
            <UiMedallion
              icon="globe"
              shape="square"
              :tone="ingressReady ? 'success' : 'warning'"
              class="shrink-0"
            />
            <span class="min-w-0">
              <span class="block text-sm font-medium text-fg-strong">Check connectivity</span>
              <span class="mt-0.5 block text-xs text-fg-muted">
                {{ ingressReady ? 'Inbound messaging is reachable.' : 'Make bots reachable from the internet.' }}
              </span>
            </span>
          </button>
        </div>
      </section>
    </template>
  </section>
</template>
