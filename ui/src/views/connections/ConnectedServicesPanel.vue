<script setup lang="ts">
import type { SchemaAuthProviderOut } from '@/api'
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
import { formatAbsoluteDateTime, formatRelativeDateTime } from '@/lib/stackos/time'

import {
  accountLabel,
  connectionTitle,
  formatAuthType,
  methodLabel,
  pluginLabel,
  serviceName,
  serviceStatusLabel,
  serviceStatusTone,
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
  <section
    class="space-y-3"
    aria-label="Connected services"
  >
    <UiSectionHeader
      title="Connected services"
      description="Each service can have multiple named connections for different accounts, workspaces, or client profiles."
      as="h3"
    >
      <template #actions>
        <UiBadge>{{ connectionsCount }}</UiBadge>
      </template>
    </UiSectionHeader>

    <UiCard
      v-if="loading"
      role="status"
      aria-label="Loading connections"
    >
      <UiSkeleton
        shape="line"
        :lines="3"
      />
    </UiCard>

    <UiEmptyState
      v-else-if="serviceGroups.length === 0"
      title="No services connected."
      description="Add the first connection for a provider account or internal tool. The daemon stores the secret and exposes only status, labels, and credential refs."
      icon="plug"
      class="rounded-lg border border-dashed border-default bg-bg-surface px-4 py-8"
    >
      <template #actions>
        <UiButton
          variant="primary"
          icon-left="plus"
          @click="$emit('add-connection')"
        >
          Add connection
        </UiButton>
      </template>
    </UiEmptyState>

    <div
      v-else
      class="grid gap-4"
    >
      <UiCard
        v-for="group in serviceGroups"
        :key="group.providerKey"
        section
        :aria-label="serviceName(group)"
      >
        <template #header>
          <div class="flex min-w-0 items-center gap-3">
            <span
              class="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent-subtle text-accent-fg"
              aria-hidden="true"
            >
              <UiIcon
                name="plug"
                class="h-[18px] w-[18px]"
              />
            </span>
            <div class="min-w-0">
              <div class="flex min-w-0 flex-wrap items-center gap-2">
                <h4 class="truncate text-sm font-semibold text-fg-strong">
                  {{ serviceName(group) }}
                </h4>
                <UiBadge
                  v-if="group.provider"
                  tone="accent"
                >
                  {{ pluginLabel(group.provider.plugin_slug) }}
                </UiBadge>
                <UiBadge
                  :tone="serviceStatusTone(group)"
                  :dot="serviceStatusTone(group) === 'success'"
                >
                  {{ serviceStatusLabel(group) }}
                </UiBadge>
              </div>
              <p class="mt-0.5 truncate text-xs text-fg-subtle">
                <span class="font-mono text-2xs">{{ group.providerKey }}</span>
                <template v-if="group.provider?.description">
                  · {{ group.provider.description }}
                </template>
              </p>
            </div>
          </div>
          <UiButton
            v-if="group.provider && canAddProvider(group.provider)"
            class="shrink-0"
            size="sm"
            variant="secondary"
            icon-left="plus"
            @click="$emit('add-connection', group.provider.key)"
          >
            Add another
          </UiButton>
        </template>

        <ul
          class="divide-y divide-border-subtle"
          :aria-label="`${serviceName(group)} connections`"
        >
          <li
            v-for="connection in group.connections"
            :key="connection.credential_ref"
            class="py-3"
          >
            <div class="flex flex-col gap-3 xl:flex-row xl:items-center">
              <div class="min-w-0 xl:flex-1">
                <div class="flex flex-wrap items-center gap-2">
                  <h5 class="truncate text-sm font-medium text-fg-strong">
                    {{ connectionTitle(connection) }}
                  </h5>
                  <UiBadge
                    :tone="statusTone(connection)"
                    :dot="statusTone(connection) === 'success'"
                  >
                    {{ connection.status }}
                  </UiBadge>
                </div>
                <p class="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-1.5 font-mono text-2xs text-fg-subtle">
                  <span class="truncate">{{ connection.credential_ref }}</span>
                  <span aria-hidden="true">·</span>
                  <span class="truncate">{{ connection.profile_key }}</span>
                </p>
              </div>

              <dl class="grid shrink-0 grid-cols-2 gap-x-6 gap-y-2 text-xs sm:flex sm:flex-wrap sm:items-center">
                <div class="min-w-0">
                  <dt class="text-fg-subtle">
                    Account
                  </dt>
                  <dd class="mt-0.5 truncate text-fg-default">
                    {{ accountLabel(connection) }}
                  </dd>
                </div>
                <div class="min-w-0">
                  <dt class="text-fg-subtle">
                    Auth
                  </dt>
                  <dd class="mt-0.5 truncate text-fg-default">
                    {{
                      group.provider
                        ? methodLabel(group.provider, connection.auth_method_key)
                        : formatAuthType(connection.auth_type)
                    }}
                  </dd>
                </div>
                <div class="min-w-0">
                  <dt class="text-fg-subtle">
                    Last tested
                  </dt>
                  <dd
                    class="mt-0.5 truncate text-fg-default"
                    :title="formatAbsoluteDateTime(connection.last_tested_at)"
                  >
                    {{ formatRelativeDateTime(connection.last_tested_at) }}
                  </dd>
                </div>
                <div
                  v-if="connection.expires_at"
                  class="min-w-0"
                >
                  <dt class="text-fg-subtle">
                    Expires
                  </dt>
                  <dd
                    class="mt-0.5 truncate text-fg-default"
                    :title="formatAbsoluteDateTime(connection.expires_at)"
                  >
                    {{ formatRelativeDateTime(connection.expires_at) }}
                  </dd>
                </div>
              </dl>

              <div class="flex shrink-0 items-center gap-1.5 xl:justify-end">
                <UiButton
                  size="sm"
                  variant="secondary"
                  icon-left="bolt"
                  :loading="busyAction === connectionActionKey(connection.credential_ref, 'test')"
                  :disabled="connection.revoked_at !== null"
                  @click="$emit('test-connection', connection)"
                >
                  Test
                </UiButton>
                <UiButton
                  size="sm"
                  variant="ghost"
                  icon-left="trash"
                  class="btn-danger-quiet"
                  :loading="busyAction === connectionActionKey(connection.credential_ref, 'revoke')"
                  :disabled="connection.revoked_at !== null"
                  @click="$emit('revoke-connection', connection)"
                >
                  Revoke
                </UiButton>
              </div>
            </div>

            <UiCallout
              v-if="connectionMessages[connection.credential_ref]"
              :tone="connectionMessages[connection.credential_ref].tone"
              density="compact"
              class="mt-3"
            >
              {{ connectionMessages[connection.credential_ref].text }}
            </UiCallout>
          </li>
        </ul>
      </UiCard>
    </div>
  </section>
</template>

<style scoped>
/* Destructive-quiet ghost button: danger text, danger-subtle hover tint. */
.btn-danger-quiet {
  color: var(--color-danger-fg);
}
.btn-danger-quiet:hover:not(:disabled),
.btn-danger-quiet:active:not(:disabled) {
  color: var(--color-danger-fg);
  background-color: var(--color-danger-subtle);
}
.btn-danger-quiet:disabled {
  color: var(--color-fg-disabled);
}
</style>
