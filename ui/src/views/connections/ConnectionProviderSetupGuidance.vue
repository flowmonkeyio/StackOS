<script setup lang="ts">
import { computed } from 'vue'

import type { SchemaAuthProviderOut } from '@/api'
import { UiCallout, UiCodeBlock } from '@/components/ui'

import { providerSetupGuidance } from './providerSetup'

const props = defineProps<{
  provider: SchemaAuthProviderOut
  editing: boolean
}>()

const guidance = computed(() => providerSetupGuidance(props.provider))
</script>

<template>
  <section
    v-if="guidance"
    class="grid gap-3 rounded-lg border border-border-subtle bg-bg-surface-alt p-3"
    :aria-label="`${provider.name} setup guidance`"
  >
    <div v-if="guidance.setupNote || guidance.localSetupNote">
      <h3 class="text-xs font-semibold text-fg-default">
        {{ guidance.localSetupLabel ?? 'Provider setup' }}
      </h3>
      <p
        v-if="guidance.setupNote"
        class="mt-1 text-xs leading-5 text-fg-muted"
      >
        {{ guidance.setupNote }}
      </p>
      <p
        v-if="guidance.localSetupNote && guidance.localSetupNote !== guidance.setupNote"
        class="mt-1 text-xs leading-5 text-fg-muted"
      >
        {{ guidance.localSetupNote }}
      </p>
    </div>

    <div
      v-if="guidance.callbackUrl"
      class="grid gap-1.5"
    >
      <p class="text-xs font-medium text-fg-default">
        OAuth callback URL
      </p>
      <UiCodeBlock
        :code="guidance.callbackUrl"
        language="URL"
        density="compact"
        wrap
        copyable
        :aria-label="`${provider.name} OAuth callback URL`"
      />
      <p
        v-if="guidance.callbackNote"
        class="text-2xs leading-5 text-fg-muted"
      >
        {{ guidance.callbackNote }}
      </p>
    </div>

    <div v-if="guidance.links.length > 0">
      <p class="text-xs font-medium text-fg-default">
        Official setup links
      </p>
      <ul class="mt-1.5 flex flex-wrap gap-x-3 gap-y-1.5">
        <li
          v-for="link in guidance.links"
          :key="link.key"
          class="text-xs"
        >
          <a
            :href="link.url"
            target="_blank"
            rel="noopener noreferrer"
            class="focus-ring rounded-sm text-fg-link underline underline-offset-2"
          >
            {{ link.label }}
            <span v-if="link.directional">(closest official route)</span>
            <span class="sr-only">(opens in a new tab)</span>
          </a>
        </li>
      </ul>
      <p
        v-if="guidance.verifiedAt"
        class="mt-1.5 text-2xs text-fg-muted"
      >
        Links verified {{ guidance.verifiedAt }}.
      </p>
    </div>

    <UiCallout
      v-if="editing && guidance.repairNote"
      tone="warning"
      density="compact"
      title="Reconnect guidance"
    >
      {{ guidance.repairNote }}
    </UiCallout>
  </section>
</template>
