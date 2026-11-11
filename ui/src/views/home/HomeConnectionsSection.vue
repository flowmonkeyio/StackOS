<script setup lang="ts">
import type { SchemaAccountOut } from '@/api'
import ProviderMark from '@/components/domain/ProviderMark.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { UiButton, UiCallout, UiCard, UiSectionHeader, UiSkeleton } from '@/components/ui'
import { connectionNeedsAttention, connectionStatusKey } from '@/views/connections/credentialPresentation'
import { providerLabel } from '@/views/connections/formatters'

defineProps<{ accounts: SchemaAccountOut[]; loading: boolean; error?: string | null; projectId?: number }>()
</script>

<template>
  <UiCard
    section
    aria-label="Connected tools"
  >
    <UiSectionHeader title="Connected tools" />
    <UiCallout
      v-if="error"
      tone="warning"
      class="mt-3"
    >
      {{ error }}
    </UiCallout>
    <UiSkeleton
      v-if="loading"
      shape="block"
      height="6rem"
      class="mt-3"
    />
    <ul
      v-else-if="accounts.length"
      class="mt-3 divide-y divide-border-subtle"
    >
      <li
        v-for="account in accounts"
        :key="account.credential_ref"
        class="flex min-w-0 items-center gap-2 py-2"
      >
        <ProviderMark
          :provider-key="account.provider_key"
          :name="providerLabel(account.provider_key)"
          size="xs"
        />
        <div class="min-w-0 flex-1">
          <p class="truncate text-sm text-fg-strong">
            {{ providerLabel(account.provider_key) }}
          </p>
          <p
            class="truncate text-2xs text-fg-muted"
            :title="account.display_name"
          >
            {{ account.display_name }}
          </p>
        </div>
        <StatusBadge
          kind="connection"
          :status="connectionStatusKey(account)"
          :label="connectionNeedsAttention(account) && account.last_test?.ok === false ? 'Test failed' : undefined"
          :tone="account.last_test?.ok === false ? 'danger' : undefined"
        />
      </li>
    </ul>
    <p
      v-else-if="!error"
      class="my-4 text-xs text-fg-muted"
    >
      {{ projectId ? 'No Accounts are attached to this project.' : 'No Accounts are connected.' }}
    </p>
    <RouterLink
      v-slot="{ href, navigate }"
      :to="projectId ? `/projects/${projectId}/connections?section=services` : '/accounts'"
      custom
    >
      <UiButton
        :href="href"
        variant="ghost"
        size="sm"
        icon-right="chevron-right"
        class="mt-3"
        @click="navigate"
      >
        Manage connections
      </UiButton>
    </RouterLink>
  </UiCard>
</template>
