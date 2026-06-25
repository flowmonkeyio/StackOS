<script setup lang="ts">
import { computed, ref } from 'vue'

import type { SchemaAuthProviderOut } from '@/api'
import {
  UiBadge,
  UiButton,
  UiCallout,
  UiCard,
  UiCountBadge,
  UiEmptyState,
  UiFilterBar,
  UiMedallion,
  UiSectionHeader,
  UiSegmentedControl,
  UiSkeleton,
} from '@/components/ui'
import StatusBadge from '@/components/StatusBadge.vue'
import { resolveStatus } from '@/design/status'
import { formatAbsoluteDateTime, formatRelativeDateTime } from '@/lib/stackos/time'

import {
  accountLabel,
  connectionNeedsAttention,
  connectionStatusKey,
  connectionTitle,
  formatAuthType,
  methodLabel,
  pluginLabel,
  providerGroupLabel,
  serviceName,
} from './formatters'
import type { ConnectionRow, MessageMap, ServiceGroup } from './types'

const props = defineProps<{
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

const search = ref('')
const category = ref('all')

function groupCategory(group: ServiceGroup): string {
  return group.provider ? providerGroupLabel(group.provider) : 'Other'
}

const categoryOptions = computed(() => {
  const categories = new Set<string>()
  for (const group of props.serviceGroups) categories.add(groupCategory(group))
  return [
    { key: 'all', label: 'All' },
    ...Array.from(categories)
      .sort((left, right) => left.localeCompare(right))
      .map((name) => ({ key: name, label: name })),
  ]
})

const visibleGroups = computed(() => {
  const query = search.value.trim().toLowerCase()
  return props.serviceGroups.filter((group) => {
    if (category.value !== 'all' && groupCategory(group) !== category.value) return false
    if (!query) return true
    const haystack = [
      serviceName(group),
      group.provider?.description ?? '',
      ...group.connections.map((connection) => connectionTitle(connection)),
      ...group.connections.map((connection) => accountLabel(connection)),
    ]
      .join(' ')
      .toLowerCase()
    return haystack.includes(query)
  })
})

function connectionActionKey(credentialRef: string, action: string): string {
  return `${credentialRef}:${action}`
}

function isAttention(connection: ConnectionRow): boolean {
  return connectionNeedsAttention(connection)
}

function authLabel(group: ServiceGroup, connection: ConnectionRow): string {
  return group.provider
    ? methodLabel(group.provider, connection.auth_method_key)
    : formatAuthType(connection.auth_type)
}

/** Only surface the account when it adds information beyond the row title. */
function showAccount(connection: ConnectionRow): boolean {
  const account = accountLabel(connection)
  return account !== '-' && account !== connectionTitle(connection)
}

const DOT_CLASS: Record<string, string> = {
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-danger',
  info: 'bg-info',
  neutral: 'bg-fg-subtle',
}

function statusDotClass(connection: ConnectionRow): string {
  return DOT_CLASS[resolveStatus('connection', connectionStatusKey(connection)).tone] ?? 'bg-fg-subtle'
}

function statusLabel(connection: ConnectionRow): string {
  return resolveStatus('connection', connectionStatusKey(connection)).label
}
</script>

<template>
  <section
    class="space-y-3"
    aria-label="Services"
  >
    <UiSectionHeader
      title="Services"
      description="Tools and accounts your agents can use. Secrets stay on this machine — agents only ever get a safe reference."
      as="h3"
    >
      <template #actions>
        <UiCountBadge :value="connectionsCount" />
      </template>
    </UiSectionHeader>

    <UiFilterBar
      v-if="!loading && serviceGroups.length > 0"
      v-model:search="search"
      search-placeholder="Find a service or account…"
      aria-label="Service filters"
    >
      <UiSegmentedControl
        v-if="categoryOptions.length > 2"
        v-model="category"
        :options="categoryOptions"
        label="Category"
      />
    </UiFilterBar>

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
      title="No services connected"
      description="Add the first connection for a provider account or internal tool. The daemon stores the secret and exposes only status, labels, and credential refs."
      icon="plug"
      framed
    >
      <template #actions>
        <UiButton
          variant="primary"
          size="sm"
          icon-left="plus"
          @click="$emit('add-connection')"
        >
          Add connection
        </UiButton>
      </template>
    </UiEmptyState>

    <UiEmptyState
      v-else-if="visibleGroups.length === 0"
      title="No services match"
      description="Try a different search or category."
      icon="search"
      framed
    />

    <div
      v-else
      class="grid grid-cols-1 gap-3"
    >
      <UiCard
        v-for="group in visibleGroups"
        :key="group.providerKey"
        section
        :padded="false"
        class="min-w-0 overflow-hidden"
        :aria-label="serviceName(group)"
      >
        <template #header>
          <div class="flex min-w-0 items-center gap-3">
            <UiMedallion
              icon="plug"
              shape="square"
              tone="neutral"
            />
            <div class="min-w-0">
              <div class="flex min-w-0 flex-wrap items-center gap-2">
                <h4 class="t-h3 truncate text-fg-strong">
                  {{ serviceName(group) }}
                </h4>
                <UiBadge
                  v-if="group.provider"
                  variant="outline"
                >
                  {{ pluginLabel(group.provider.plugin_slug) }}
                </UiBadge>
              </div>
              <p
                v-if="group.provider?.description"
                class="mt-0.5 truncate text-xs text-fg-subtle"
              >
                {{ group.provider.description }}
              </p>
            </div>
          </div>
          <UiButton
            v-if="group.provider && canAddProvider(group.provider)"
            class="shrink-0"
            size="sm"
            variant="ghost"
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
            class="px-4 py-2.5"
            :class="isAttention(connection) ? 'bg-warning-subtle' : ''"
          >
            <div class="flex items-center gap-3">
              <span
                role="img"
                :aria-label="statusLabel(connection)"
                :title="statusLabel(connection)"
                class="h-2 w-2 shrink-0 rounded-full"
                :class="statusDotClass(connection)"
              />
              <div class="min-w-0 flex-1">
                <div class="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                  <span class="truncate text-sm font-medium text-fg-strong">
                    {{ connectionTitle(connection) }}
                  </span>
                  <StatusBadge
                    v-if="isAttention(connection)"
                    domain="connection"
                    :status="connectionStatusKey(connection)"
                  />
                </div>
                <p class="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-xs text-fg-muted">
                  <span>{{ authLabel(group, connection) }}</span>
                  <span
                    aria-hidden="true"
                    class="text-fg-subtle"
                  >·</span>
                  <span :title="formatAbsoluteDateTime(connection.last_tested_at)">
                    {{
                      connection.last_tested_at
                        ? `tested ${formatRelativeDateTime(connection.last_tested_at)}`
                        : 'never tested'
                    }}
                  </span>
                  <template v-if="connection.expires_at">
                    <span
                      aria-hidden="true"
                      class="text-fg-subtle"
                    >·</span>
                    <span :title="formatAbsoluteDateTime(connection.expires_at)">
                      expires {{ formatRelativeDateTime(connection.expires_at) }}
                    </span>
                  </template>
                  <template v-if="showAccount(connection)">
                    <span
                      aria-hidden="true"
                      class="text-fg-subtle"
                    >·</span>
                    <span class="truncate font-mono text-2xs">{{ accountLabel(connection) }}</span>
                  </template>
                </p>
              </div>
              <div class="flex shrink-0 items-center gap-1">
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
                  variant="danger-ghost"
                  icon-left="trash"
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
              class="mt-2"
            >
              {{ connectionMessages[connection.credential_ref].text }}
            </UiCallout>
          </li>
        </ul>
      </UiCard>
    </div>
  </section>
</template>
