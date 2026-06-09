<script setup lang="ts">
import {
  UiBadge,
  UiButton,
  UiCallout,
  UiCard,
  UiEmptyState,
  UiIcon,
  UiSectionHeader,
  UiSkeleton,
} from '@/components/ui'

import {
  commandSummary,
  telegramCommands,
  telegramProfileAuthKey,
  telegramProfileIngressMode,
  telegramProfileUsername,
} from './formatters'
import type { CommunicationProfile, ConnectionRow, MessageTone } from './types'

defineProps<{
  telegramConnections: ConnectionRow[]
  telegramProfiles: CommunicationProfile[]
  loading: boolean
  message: { tone: MessageTone; text: string } | null
}>()

defineEmits<{
  (e: 'add-connection', providerKey: string): void
  (e: 'add-profile'): void
  (e: 'edit-profile', profile: CommunicationProfile): void
}>()
</script>

<template>
  <section
    class="space-y-3"
    aria-label="Telegram profiles"
  >
    <UiSectionHeader
      title="Telegram profiles"
      description="Bind a Telegram connection to project-scoped identity, agent guidance, access, trigger, context, and response policy."
      as="h3"
    >
      <template #actions>
        <UiBadge>{{ telegramProfiles.length }}</UiBadge>
        <UiButton
          size="sm"
          variant="secondary"
          icon-left="plus"
          :disabled="telegramConnections.length === 0"
          @click="$emit('add-profile')"
        >
          Add Telegram profile
        </UiButton>
      </template>
    </UiSectionHeader>

    <UiCallout
      v-if="telegramConnections.length === 0"
      tone="info"
    >
      Store a Telegram Bot connection before creating a Telegram profile.
      <template #actions>
        <UiButton
          size="sm"
          variant="secondary"
          icon-left="plus"
          @click="$emit('add-connection', 'telegram-bot')"
        >
          Add Telegram connection
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
      aria-label="Loading Telegram profiles"
    >
      <UiSkeleton
        shape="line"
        :lines="3"
      />
    </UiCard>

    <UiCard
      v-else-if="telegramProfiles.length > 0"
      section
      aria-label="Telegram profile list"
    >
      <ul class="divide-y divide-border-subtle">
        <li
          v-for="profile in telegramProfiles"
          :key="profile.profile_ref"
          class="py-3"
        >
          <div class="flex flex-col gap-3 lg:flex-row lg:items-center">
            <div class="flex min-w-0 items-center gap-3 lg:flex-1">
              <span
                class="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent-subtle text-accent-fg"
                aria-hidden="true"
              >
                <UiIcon
                  name="chat"
                  class="h-[18px] w-[18px]"
                />
              </span>
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <h4 class="truncate text-sm font-medium text-fg-strong">
                    {{ profile.identity.display_name || profile.key }}
                  </h4>
                  <UiBadge
                    :tone="profile.enabled ? 'success' : 'warning'"
                    :dot="profile.enabled"
                  >
                    {{ profile.enabled ? 'enabled' : 'disabled' }}
                  </UiBadge>
                  <UiBadge>{{ telegramProfileIngressMode(profile) }}</UiBadge>
                </div>
                <p class="mt-0.5 truncate font-mono text-2xs text-fg-subtle">
                  {{ profile.key }} · {{ telegramProfileAuthKey(profile) }}
                  <template v-if="telegramProfileUsername(profile)">
                    · @{{ telegramProfileUsername(profile) }}
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
                  {{ profile.access_policy.allowed_chat_refs?.length ?? 0 }}
                </dd>
              </div>
              <div>
                <dt class="text-fg-subtle">
                  Users
                </dt>
                <dd class="mt-0.5 font-medium tabular-nums text-fg-default">
                  {{ profile.access_policy.allowed_user_refs?.length ?? 0 }}
                </dd>
              </div>
              <div class="min-w-0 lg:max-w-48">
                <dt class="text-fg-subtle">
                  Commands
                </dt>
                <dd class="mt-0.5 truncate font-mono text-2xs text-fg-default">
                  {{ commandSummary(telegramCommands(profile)) }}
                </dd>
              </div>
            </dl>

            <div class="flex shrink-0 lg:justify-end">
              <UiButton
                size="sm"
                variant="secondary"
                icon-left="settings"
                @click="$emit('edit-profile', profile)"
              >
                Configure
              </UiButton>
            </div>
          </div>
        </li>
      </ul>
    </UiCard>

    <UiEmptyState
      v-else-if="telegramConnections.length > 0"
      title="No Telegram profiles configured"
      description="Create a profile for each Telegram bot identity or access boundary. Profiles are static setup; agents still decide which work to run after a trigger arrives."
      icon="chat"
      class="rounded-lg border border-dashed border-default bg-bg-surface px-4 py-8"
    />
  </section>
</template>
