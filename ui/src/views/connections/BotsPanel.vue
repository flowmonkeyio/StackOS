<script setup lang="ts">
import { computed } from 'vue'

import {
  UiBadge,
  UiButton,
  UiCallout,
  UiCard,
  UiCountBadge,
  UiDropdownMenu,
  UiEmptyState,
  UiMedallion,
  UiSectionHeader,
  UiSkeleton,
} from '@/components/ui'
import StatusBadge from '@/components/StatusBadge.vue'

import {
  communicationProfileTitle,
  profileAudienceMeta,
  profilePrimaryProvider,
  providerLabel,
} from './formatters'
import type { CommunicationProfile, ConnectionRow, MessageTone } from './types'

const props = defineProps<{
  bots: CommunicationProfile[]
  slackConnections: ConnectionRow[]
  loading: boolean
  message: { tone: MessageTone; text: string } | null
}>()

defineEmits<{
  (e: 'add-connection', providerKey: string): void
  (e: 'add-bot', provider: string): void
  (e: 'edit-bot', profile: CommunicationProfile): void
}>()

const addBotItems = computed(() => [
  {
    key: 'slack-bot',
    label: 'Slack bot',
    icon: 'chat',
    disabled: props.slackConnections.length === 0,
  },
])

const hasMessagingConnection = computed(
  () => props.slackConnections.length > 0,
)

/** The dedicated editor is retained only for Slack's signed HTTP ingress profile. */
function isEditable(bot: CommunicationProfile): boolean {
  return Boolean(bot.provider_facets?.['slack-bot'])
}

function botProviderLabel(bot: CommunicationProfile): string {
  return providerLabel(profilePrimaryProvider(bot))
}

function userCount(bot: CommunicationProfile): number {
  return bot.access_policy.allowed_user_refs?.length ?? 0
}
</script>

<template>
  <section class="space-y-3" aria-label="Bots">
    <UiSectionHeader
      title="Bots"
      description="A messaging identity carries agent guidance, access rules, and reply policy. Telegram bot and user sessions are Account-bound TDLib connections managed under Services."
      as="h3"
    >
      <template #actions>
        <UiCountBadge :value="bots.length" />
        <UiDropdownMenu
          :items="addBotItems"
          placement="bottom-end"
          aria-label="Add a bot"
          @select="(item) => $emit('add-bot', item.key)"
        >
          <template #trigger="{ toggle, open }">
            <UiButton
              size="sm"
              variant="secondary"
              icon-left="plus"
              icon-right="chevron-down"
              :disabled="!hasMessagingConnection"
              :aria-expanded="open"
              data-dropdown-trigger
              @click="toggle"
            >
              Add bot
            </UiButton>
          </template>
        </UiDropdownMenu>
      </template>
    </UiSectionHeader>

    <UiCallout v-if="!hasMessagingConnection && bots.length === 0" tone="info">
      Connect a Slack bot first, then give it an identity and access rules here. Telegram bot and
      user sessions are configured as Accounts under Services.
      <template #actions>
        <UiButton
          size="sm"
          variant="secondary"
          icon-left="plus"
          @click="$emit('add-connection', 'slack-bot')"
        >
          Connect Slack
        </UiButton>
      </template>
    </UiCallout>

    <UiCallout v-else-if="message" :tone="message.tone">
      {{ message.text }}
    </UiCallout>

    <UiCard v-if="loading" aria-label="Loading bots">
      <UiSkeleton shape="line" :lines="3" />
    </UiCard>

    <UiCard
      v-else-if="bots.length > 0"
      section
      :padded="false"
      class="overflow-hidden"
      aria-label="Bot list"
    >
      <ul class="divide-y divide-border-subtle">
        <li
          v-for="bot in bots"
          :key="bot.profile_ref"
          class="px-4 py-3"
          :class="bot.binding_status === 'repair-required' ? 'bg-warning-subtle' : ''"
        >
          <div class="flex flex-col gap-3 lg:flex-row lg:items-center">
            <div class="flex min-w-0 items-center gap-3 lg:flex-1">
              <UiMedallion icon="chat" tone="info" class="shrink-0" />
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <h4 class="truncate text-sm font-medium text-fg-strong">
                    {{ communicationProfileTitle(bot) }}
                  </h4>
                  <UiBadge tone="accent">
                    {{ botProviderLabel(bot) }}
                  </UiBadge>
                  <StatusBadge domain="step" :status="bot.enabled ? 'enabled' : 'disabled'" />
                </div>
                <p class="mt-0.5 truncate font-mono text-2xs text-fg-subtle">
                  {{ bot.key }}
                </p>
              </div>
            </div>

            <dl class="grid shrink-0 grid-cols-3 gap-x-6 text-xs lg:flex lg:items-center">
              <div>
                <dt class="text-fg-subtle">
                  {{ profileAudienceMeta(bot).label }}
                </dt>
                <dd class="mt-0.5 font-medium tabular-nums text-fg-default">
                  {{ profileAudienceMeta(bot).count }}
                </dd>
              </div>
              <div>
                <dt class="text-fg-subtle">Users</dt>
                <dd class="mt-0.5 font-medium tabular-nums text-fg-default">
                  {{ userCount(bot) }}
                </dd>
              </div>
            </dl>

            <div class="flex shrink-0 items-center lg:justify-end">
              <UiButton
                v-if="isEditable(bot)"
                size="sm"
                variant="secondary"
                icon-left="settings"
                @click="$emit('edit-bot', bot)"
              >
                Configure
              </UiButton>
              <UiBadge v-else variant="outline"> View only </UiBadge>
            </div>
          </div>
          <UiCallout
            v-if="bot.binding_status === 'repair-required'"
            tone="warning"
            density="compact"
            class="mt-3"
            title="Account binding needs repair"
          >
            {{ bot.binding_issues.map((issue) => issue.message).join(' ') }}
          </UiCallout>
        </li>
      </ul>
    </UiCard>

    <UiEmptyState
      v-else-if="hasMessagingConnection"
      title="No bots yet"
      description="Create a bot for each messaging identity or access boundary. Bots are static setup — agents still decide what work to run after a trigger arrives."
      icon="chat"
      framed
    />
  </section>
</template>
