<script setup lang="ts">
import { UiBadge, UiButton, UiCallout, UiPanel, UiSectionHeader } from '@/components/ui'

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
  <UiPanel class="p-4">
    <UiSectionHeader
      title="Telegram Profiles"
      description="Bind a Telegram connection to project-scoped identity, agent guidance, access, trigger, context, and response policy."
    >
      <template #actions>
        <div class="flex flex-wrap items-center gap-2">
          <UiBadge>{{ telegramProfiles.length }}</UiBadge>
          <UiButton
            size="sm"
            icon-left="plus"
            :disabled="telegramConnections.length === 0"
            @click="$emit('add-profile')"
          >
            Add Telegram profile
          </UiButton>
        </div>
      </template>
    </UiSectionHeader>

    <UiCallout v-if="telegramConnections.length === 0" tone="info">
      Store a Telegram Bot connection before creating a Telegram profile.
      <UiButton
        class="mt-3"
        size="sm"
        icon-left="plus"
        @click="$emit('add-connection', 'telegram-bot')"
      >
        Add Telegram connection
      </UiButton>
    </UiCallout>

    <UiCallout v-else-if="message" :tone="message.tone">
      {{ message.text }}
    </UiCallout>

    <div
      v-if="loading"
      class="rounded-md border border-subtle bg-bg-surface p-4 text-sm text-fg-muted"
    >
      Loading Telegram profiles...
    </div>

    <div
      v-else-if="telegramConnections.length > 0 && telegramProfiles.length === 0"
      class="rounded-md border border-dashed border-default bg-bg-surface p-5"
    >
      <p class="font-medium text-fg-strong">No Telegram profiles configured.</p>
      <p class="mt-1 max-w-3xl text-sm text-fg-muted">
        Create a profile for each Telegram bot identity or access boundary. Profiles are static
        setup; agents still decide which work to run after a trigger arrives.
      </p>
    </div>

    <ul v-else class="grid gap-3">
      <li
        v-for="profile in telegramProfiles"
        :key="profile.profile_ref"
        class="rounded-md border border-subtle bg-bg-surface px-4 py-4"
      >
        <div
          class="grid gap-3 lg:grid-cols-[minmax(0,1.2fr)_minmax(20rem,1fr)_auto] lg:items-center"
        >
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <h3 class="text-sm font-semibold text-fg-strong">
                {{ profile.identity.display_name || profile.key }}
              </h3>
              <UiBadge :tone="profile.enabled ? 'success' : 'warning'">
                {{ profile.enabled ? 'enabled' : 'disabled' }}
              </UiBadge>
              <UiBadge>{{ telegramProfileIngressMode(profile) }}</UiBadge>
            </div>
            <p class="mt-1 truncate text-xs text-fg-muted">
              <span class="font-mono">{{ profile.key }}</span>
              <span aria-hidden="true"> &middot; </span>Connection
              <span class="font-mono">{{ telegramProfileAuthKey(profile) }}</span>
              <template v-if="telegramProfileUsername(profile)">
                <span aria-hidden="true"> &middot; </span>@{{ telegramProfileUsername(profile) }}
              </template>
            </p>
          </div>
          <dl class="grid gap-3 text-sm sm:grid-cols-3">
            <div>
              <dt class="text-2xs font-medium uppercase text-fg-muted">Chats</dt>
              <dd class="mt-0.5 font-mono text-xs text-fg-default">
                {{ profile.access_policy.allowed_chat_refs?.length ?? 0 }}
              </dd>
            </div>
            <div>
              <dt class="text-2xs font-medium uppercase text-fg-muted">Users</dt>
              <dd class="mt-0.5 font-mono text-xs text-fg-default">
                {{ profile.access_policy.allowed_user_refs?.length ?? 0 }}
              </dd>
            </div>
            <div>
              <dt class="text-2xs font-medium uppercase text-fg-muted">Commands</dt>
              <dd class="mt-0.5 truncate font-mono text-xs text-fg-default">
                {{ commandSummary(telegramCommands(profile)) }}
              </dd>
            </div>
          </dl>
          <div class="flex justify-start lg:justify-end">
            <UiButton size="sm" icon-left="settings" @click="$emit('edit-profile', profile)">
              Configure
            </UiButton>
          </div>
        </div>
      </li>
    </ul>
  </UiPanel>
</template>
