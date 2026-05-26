<script setup lang="ts">
import type { SchemaAuthProviderOut } from '@/api'
import { UiBadge, UiButton, UiCallout, UiPanel, UiSectionHeader } from '@/components/ui'
import { formatDateTime } from '@/lib/stackos/json'

import {
  accountLabel,
  connectionCountLabel,
  connectionTitle,
  formatAuthType,
  methodLabel,
  pluginLabel,
  serviceName,
  serviceStatusDotClass,
  serviceStatusLabel,
  serviceStatusTone,
  statusDotClass,
  statusTone,
} from './formatters'
import type { ConnectionRow, MessageMap, ServiceGroup } from './types'

defineProps<{
  loading: boolean
  serviceGroups: ServiceGroup[]
  connectionsCount: number
  connectionMessages: MessageMap
  busyAction: string | null
  canAddProvider: (provider: SchemaAuthProviderOut) => boolean
}>()

defineEmits<{
  (e: 'add-connection', providerKey?: string): void
  (e: 'test-connection', connection: ConnectionRow): void
  (e: 'revoke-connection', connection: ConnectionRow): void
}>()

function connectionActionKey(credentialRef: string, action: string): string {
  return `${credentialRef}:${action}`
}
</script>

<template>
  <UiPanel class="p-4">
    <UiSectionHeader
      title="Connected Services"
      description="Each service can have multiple named connections for different accounts, workspaces, or client profiles."
    >
      <template #actions>
        <UiBadge>{{ connectionsCount }}</UiBadge>
      </template>
    </UiSectionHeader>

    <div
      v-if="loading"
      class="rounded-md border border-subtle bg-bg-surface p-4 text-sm text-fg-muted"
    >
      Loading connections...
    </div>

    <div
      v-else-if="serviceGroups.length === 0"
      class="rounded-md border border-dashed border-default bg-bg-surface p-6 text-center"
    >
      <p class="font-medium text-fg-strong">No services connected.</p>
      <p class="mx-auto mt-1 max-w-xl text-sm text-fg-muted">
        Add the first connection for a provider account or internal tool. The daemon stores the
        secret and exposes only status, labels, and credential refs.
      </p>
      <UiButton class="mt-4" variant="primary" icon-left="plus" @click="$emit('add-connection')">
        Add connection
      </UiButton>
    </div>

    <ul v-else class="grid gap-3">
      <li
        v-for="group in serviceGroups"
        :key="group.providerKey"
        class="overflow-hidden rounded-md border border-subtle bg-bg-surface shadow-xs"
      >
        <div class="border-b border-subtle bg-bg-surface-alt px-4 py-4 sm:px-5">
          <div class="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div class="flex min-w-0 gap-3">
              <span
                :class="['mt-1 h-2.5 w-2.5 shrink-0 rounded-full', serviceStatusDotClass(group)]"
                aria-hidden="true"
              />
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <h3 class="text-base font-semibold leading-6 text-fg-strong">
                    {{ serviceName(group) }}
                  </h3>
                  <UiBadge v-if="group.provider" tone="accent">
                    {{ pluginLabel(group.provider.plugin_slug) }}
                  </UiBadge>
                  <UiBadge :tone="serviceStatusTone(group)">
                    {{ serviceStatusLabel(group) }}
                  </UiBadge>
                </div>
                <p
                  v-if="group.provider?.description"
                  class="mt-1 max-w-3xl text-sm leading-5 text-fg-muted"
                >
                  {{ group.provider.description }}
                </p>
                <dl class="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
                  <div class="flex min-w-0 items-center gap-1.5">
                    <dt class="shrink-0 text-fg-muted">Provider</dt>
                    <dd class="truncate font-mono text-fg-default">{{ group.providerKey }}</dd>
                  </div>
                  <div v-if="group.provider" class="flex items-center gap-1.5">
                    <dt class="text-fg-muted">Auth</dt>
                    <dd class="text-fg-default">
                      {{ formatAuthType(group.provider.auth_type) }}
                    </dd>
                  </div>
                  <div class="flex items-center gap-1.5">
                    <dt class="text-fg-muted">Saved</dt>
                    <dd class="text-fg-default">{{ connectionCountLabel(group) }}</dd>
                  </div>
                </dl>
              </div>
            </div>
            <UiButton
              v-if="group.provider && canAddProvider(group.provider)"
              class="shrink-0"
              size="sm"
              icon-left="plus"
              @click="$emit('add-connection', group.provider.key)"
            >
              Add another
            </UiButton>
          </div>
        </div>

        <div class="divide-y divide-subtle">
          <article
            v-for="connection in group.connections"
            :key="connection.credential_ref"
            class="grid gap-3 px-4 py-4 sm:px-5 xl:grid-cols-[minmax(0,1.1fr)_minmax(26rem,1.5fr)_auto] xl:items-center"
          >
            <div class="flex min-w-0 gap-3">
              <span
                :class="['mt-2 h-2 w-2 shrink-0 rounded-full', statusDotClass(connection)]"
                aria-hidden="true"
              />
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <h4 class="truncate text-sm font-semibold leading-5 text-fg-strong">
                    {{ connectionTitle(connection) }}
                  </h4>
                  <UiBadge :tone="statusTone(connection)">
                    {{ connection.status }}
                  </UiBadge>
                </div>
                <div class="mt-1 flex min-w-0 flex-wrap items-center gap-1.5 text-xs text-fg-muted">
                  <span class="truncate font-mono">{{ connection.credential_ref }}</span>
                  <span aria-hidden="true">&middot;</span>
                  <span>{{ formatAuthType(connection.auth_type) }}</span>
                  <template
                    v-if="
                      group.provider &&
                      methodLabel(group.provider, connection.auth_method_key) !==
                        formatAuthType(connection.auth_type)
                    "
                  >
                    <span aria-hidden="true">&middot;</span>
                    <span>{{ methodLabel(group.provider, connection.auth_method_key) }}</span>
                  </template>
                </div>
              </div>
            </div>

            <dl class="grid gap-3 text-sm sm:grid-cols-2 2xl:grid-cols-4">
              <div class="min-w-0">
                <dt class="text-2xs font-medium uppercase text-fg-muted">Connection name</dt>
                <dd class="mt-0.5 truncate font-mono text-xs text-fg-default">
                  {{ connection.profile_key }}
                </dd>
              </div>
              <div class="min-w-0">
                <dt class="text-2xs font-medium uppercase text-fg-muted">Account</dt>
                <dd class="mt-0.5 truncate text-fg-default">{{ accountLabel(connection) }}</dd>
              </div>
              <div class="min-w-0">
                <dt class="text-2xs font-medium uppercase text-fg-muted">Expires</dt>
                <dd class="mt-0.5 truncate text-fg-default">
                  {{ formatDateTime(connection.expires_at) }}
                </dd>
              </div>
              <div class="min-w-0">
                <dt class="text-2xs font-medium uppercase text-fg-muted">Last tested</dt>
                <dd class="mt-0.5 truncate text-fg-default">
                  {{ formatDateTime(connection.last_tested_at) }}
                </dd>
              </div>
            </dl>

            <div class="flex shrink-0 flex-wrap gap-2 xl:justify-end">
              <UiButton
                size="sm"
                icon-left="plug-zap"
                :loading="busyAction === connectionActionKey(connection.credential_ref, 'test')"
                :disabled="connection.revoked_at !== null"
                @click="$emit('test-connection', connection)"
              >
                Test
              </UiButton>
              <UiButton
                size="sm"
                variant="danger"
                icon-left="ban"
                :loading="busyAction === connectionActionKey(connection.credential_ref, 'revoke')"
                :disabled="connection.revoked_at !== null"
                @click="$emit('revoke-connection', connection)"
              >
                Revoke
              </UiButton>
            </div>

            <UiCallout
              v-if="connectionMessages[connection.credential_ref]"
              :tone="connectionMessages[connection.credential_ref].tone"
              class="xl:col-span-3"
            >
              {{ connectionMessages[connection.credential_ref].text }}
            </UiCallout>
          </article>
        </div>
      </li>
    </ul>
  </UiPanel>
</template>
