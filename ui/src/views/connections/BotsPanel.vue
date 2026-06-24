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
  commandSummary,
  communicationProfileTitle,
  profilePrimaryProvider,
  providerLabel,
  telegramCommands,
  telegramProfileAuthKey,
  telegramProfileIngressMode,
  telegramProfileUsername,
} from './formatters'
import type { CommunicationProfile, ConnectionRow, MessageTone } from './types'

defineProps<{
  bots: CommunicationProfile[]
  telegramConnections: ConnectionRow[]
  loading: boolean
  message: { tone: MessageTone; text: string } | null
}>()

defineEmits<{
  (e: 'add-connection', providerKey: string): void
  (e: 'add-bot'): void
  (e: 'edit-bot', profile: CommunicationProfile): void
}>()

function isTelegram(bot: CommunicationProfile): boolean {
  return Boolean(bot.provider_facets?.['telegram-bot'])
}

function botProviderLabel(bot: CommunicationProfile): string {
  // A bot can carry several provider facets (e.g. Telegram + Slack). The
  // editable surface today is Telegram, so prefer it for the headline label.
  return providerLabel(isTelegram(bot) ? 'telegram-bot' : profilePrimaryProvider(bot))
}

function userCount(bot: CommunicationProfile): number {
  return bot.access_policy.allowed_user_refs?.length ?? 0
}

function chatCount(bot: CommunicationProfile): number {
  return bot.access_policy.allowed_chat_refs?.length ?? 0
}
</script>

<template>
  <section
    class="space-y-3"
    aria-label="Bots"
  >
    <UiSectionHeader
      title="Bots"
      description="A bot is a messaging identity for your agents — who can trigger it, what it can do, and how it replies. Secrets stay in the linked connection."
      as="h3"
    >
      <template #actions>
        <UiCountBadge :value="bots.length" />
        <UiButton
          size="sm"
          variant="secondary"
          icon-left="plus"
          :disabled="telegramConnections.length === 0"
          @click="$emit('add-bot')"
        >
          Add bot
        </UiButton>
      </template>
    </UiSectionHeader>

    <UiCallout
      v-if="telegramConnections.length === 0 && bots.length === 0"
      tone="info"
    >
      Connect a Telegram bot first, then give it an identity and access rules here.
      <template #actions>
        <UiButton
          size="sm"
          variant="secondary"
          icon-left="plus"
          @click="$emit('add-connection', 'telegram-bot')"
        >
          Connect Telegram
        </UiButton>
      </template>
    </UiCallout>

    <UiCallout
      v-else-if="message"
      :tone="message.tone"
    >
      {{ message.text }}
    </UiCallout>

    <UiCard
      v-if="loading"
      aria-label="Loading bots"
    >
      <UiSkeleton
        shape="line"
        :lines="3"
      />
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
        >
          <div class="flex flex-col gap-3 lg:flex-row lg:items-center">
            <div class="flex min-w-0 items-center gap-3 lg:flex-1">
              <UiMedallion
                icon="chat"
                tone="info"
                class="shrink-0"
              />
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <h4 class="truncate text-sm font-medium text-fg-strong">
                    {{ communicationProfileTitle(bot) }}
                  </h4>
                  <UiBadge tone="accent">
                    {{ botProviderLabel(bot) }}
                  </UiBadge>
                  <StatusBadge
                    domain="step"
                    :status="bot.enabled ? 'enabled' : 'disabled'"
                  />
                  <UiBadge
                    v-if="isTelegram(bot)"
                    variant="outline"
                  >
                    {{ telegramProfileIngressMode(bot) }}
                  </UiBadge>
                </div>
                <p class="mt-0.5 truncate font-mono text-2xs text-fg-subtle">
                  {{ bot.key }}
                  <template v-if="isTelegram(bot)">
                    · {{ telegramProfileAuthKey(bot) }}
                    <template v-if="telegramProfileUsername(bot)">
                      · @{{ telegramProfileUsername(bot) }}
                    </template>
                  </template>
                </p>
              </div>
            </div>

            <dl class="grid shrink-0 grid-cols-3 gap-x-6 text-xs lg:flex lg:items-center">
              <div>
                <dt class="text-fg-subtle">
                  Chats
                </dt>
                <dd class="mt-0.5 font-medium tabular-nums text-fg-default">
                  {{ chatCount(bot) }}
                </dd>
              </div>
              <div>
                <dt class="text-fg-subtle">
                  Users
                </dt>
                <dd class="mt-0.5 font-medium tabular-nums text-fg-default">
                  {{ userCount(bot) }}
                </dd>
              </div>
              <div
                v-if="isTelegram(bot)"
                class="min-w-0 lg:max-w-48"
              >
                <dt class="text-fg-subtle">
                  Commands
                </dt>
                <dd class="mt-0.5 truncate font-mono text-2xs text-fg-default">
                  {{ commandSummary(telegramCommands(bot)) }}
                </dd>
              </div>
            </dl>

            <div class="flex shrink-0 items-center lg:justify-end">
              <UiButton
                v-if="isTelegram(bot)"
                size="sm"
                variant="secondary"
                icon-left="settings"
                @click="$emit('edit-bot', bot)"
              >
                Configure
              </UiButton>
              <UiBadge
                v-else
                variant="outline"
              >
                View only
              </UiBadge>
            </div>
          </div>
        </li>
      </ul>
    </UiCard>

    <UiEmptyState
      v-else-if="telegramConnections.length > 0"
      title="No bots yet"
      description="Create a bot for each messaging identity or access boundary. Bots are static setup — agents still decide what work to run after a trigger arrives."
      icon="chat"
      framed
    />
  </section>
</template>
