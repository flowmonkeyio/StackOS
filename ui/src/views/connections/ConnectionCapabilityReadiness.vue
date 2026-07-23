<script setup lang="ts">
import { computed } from 'vue'

import type { SchemaAuthProviderOut } from '@/api'
import StatusBadge from '@/components/StatusBadge.vue'

import { humanizeIdentifier, providerCapabilityReadiness } from './providerReadiness'
import type { ConnectionRow } from './types'

const props = defineProps<{
  provider: SchemaAuthProviderOut
  connection: ConnectionRow
}>()

const capabilities = computed(() => providerCapabilityReadiness(props.provider, props.connection))
</script>

<template>
  <section
    v-if="capabilities.length > 0"
    class="mt-3 border-t border-border-subtle pt-3"
    :aria-label="`${provider.name} capability readiness`"
  >
    <div class="mb-2">
      <h5 class="text-xs font-semibold text-fg-default">
        Capability readiness
      </h5>
      <p class="mt-0.5 text-2xs text-fg-muted">
        Based on the scopes returned by the provider and explicit setup prerequisites.
      </p>
    </div>
    <ul
      class="divide-y divide-border-subtle rounded-md border border-border-subtle bg-bg-surface-alt"
    >
      <li
        v-for="capability in capabilities"
        :key="capability.key"
        class="px-3 py-2.5"
      >
        <div class="flex flex-wrap items-center justify-between gap-2">
          <span class="text-xs font-medium text-fg-strong">{{ capability.label }}</span>
          <StatusBadge
            domain="readiness"
            :status="capability.state"
          />
        </div>
        <p class="mt-1 text-xs leading-5 text-fg-muted">
          {{ capability.summary }}
        </p>
        <details
          v-if="capability.missingScopes.length > 0"
          class="group mt-1.5"
        >
          <summary
            class="focus-ring w-fit cursor-pointer rounded-sm text-2xs font-medium text-fg-link"
          >
            {{ capability.missingScopes.length }} missing
            {{ capability.missingScopes.length === 1 ? 'scope' : 'scopes' }}
          </summary>
          <ul
            class="mt-1.5 grid gap-1 pl-4"
            aria-label="Missing OAuth scopes"
          >
            <li
              v-for="scope in capability.missingScopes"
              :key="scope"
              class="break-all font-mono text-2xs text-fg-muted"
            >
              {{ scope }}
            </li>
          </ul>
        </details>
        <p
          v-if="capability.prerequisites.length > 0"
          class="mt-1.5 text-2xs text-fg-muted"
        >
          <span class="font-medium text-fg-default">Verify:</span>
          {{ capability.prerequisites.map(humanizeIdentifier).join(', ') }}
        </p>
      </li>
    </ul>
  </section>
</template>
