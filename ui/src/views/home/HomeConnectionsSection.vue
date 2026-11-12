<script setup lang="ts">
import { computed } from 'vue'
import type { SchemaAccountOut } from '@/api'
import ProviderMark from '@/components/domain/ProviderMark.vue'
import { UiButton, UiCallout, UiCard, UiIcon, UiSectionHeader, UiSkeleton, UiTooltip } from '@/components/ui'
import { resolveStatus } from '@/design/status'
import { connectionNeedsAttention, connectionStatusKey } from '@/views/connections/credentialPresentation'
import { providerLabel } from '@/views/connections/formatters'

const props = defineProps<{ accounts: SchemaAccountOut[]; loading: boolean; error?: string | null; projectId?: number }>()

const entries = computed(() => props.accounts.map(account => {
  const provider = providerLabel(account.provider_key)
  const name = account.display_name.trim() || `Account #${account.credential_id}`
  const duplicate = props.accounts.some(other => other.credential_ref !== account.credential_ref
    && other.provider_key === account.provider_key && other.display_name.trim() === account.display_name.trim())
  const identity = duplicate ? `${name} · #${account.credential_id}` : name
  const failedTest = connectionNeedsAttention(account) && account.last_test?.ok === false
  const state = resolveStatus('connection', failedTest ? 'failed' : connectionStatusKey(account))
  const label = failedTest ? 'Test failed' : state.label
  return { account, provider, identity, state, label, description: `${provider} · ${identity} · ${label}` }
}))

const stateColor = {
  success: 'text-success-fg', warning: 'text-warning-fg', danger: 'text-danger-fg',
  info: 'text-info-fg', neutral: 'text-fg-muted',
}
</script>

<template>
  <UiCard
    section
    class="home-connections"
    aria-label="Connected tools"
  >
    <UiSectionHeader title="Connected tools">
      <template #actions>
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
            @click="navigate"
          >
            Manage connections
          </UiButton>
        </RouterLink>
      </template>
    </UiSectionHeader>
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
      height="3rem"
      class="mt-3"
    />
    <ul
      v-else-if="entries.length"
      class="home-connections__directory"
    >
      <li
        v-for="entry in entries"
        :key="entry.account.credential_ref"
        class="min-w-0"
      >
        <UiTooltip
          :content="entry.description"
          class="w-full min-w-0"
        >
          <div
            tabindex="0"
            :aria-label="entry.description"
            class="focus-ring flex h-12 w-full min-w-0 items-center gap-2 rounded-sm"
          >
            <ProviderMark
              :provider-key="entry.account.provider_key"
              :name="entry.provider"
              size="xs"
              class="home-connections__mark"
            />
            <div class="min-w-0 flex-1">
              <p class="truncate text-xs font-medium text-fg-strong">
                {{ entry.provider }}
              </p>
              <p class="truncate text-2xs text-fg-muted">
                {{ entry.identity }}
              </p>
            </div>
            <span
              role="img"
              :aria-label="entry.label"
              :title="entry.label"
              class="shrink-0"
              :class="stateColor[entry.state.tone]"
            >
              <UiIcon
                :name="entry.state.icon ?? 'circle'"
                :size="14"
              />
            </span>
          </div>
        </UiTooltip>
      </li>
    </ul>
    <p
      v-else-if="!error"
      class="my-4 text-xs text-fg-muted"
    >
      {{ projectId ? 'No Accounts are attached to this project.' : 'No Accounts are connected.' }}
    </p>
  </UiCard>
</template>

<style scoped>
.home-connections { container-type: inline-size; }
.home-connections__directory { display: grid; grid-template-columns: minmax(0, 1fr); column-gap: 1.5rem; }
.home-connections .home-connections__mark { width: 24px; height: 24px; }
.home-connections :deep(.ui-tooltip__bubble) { max-width: min(28rem, 80cqw); white-space: normal; overflow-wrap: anywhere; }
@container (min-width: 24rem) {
  .home-connections__directory { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@container (min-width: 44rem) {
  .home-connections__directory { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@container (min-width: 64rem) {
  .home-connections__directory { grid-template-columns: repeat(4, minmax(0, 1fr)); }
}
</style>
