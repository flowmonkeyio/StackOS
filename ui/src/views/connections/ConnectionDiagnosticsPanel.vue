<script setup lang="ts">
import { UiJsonBlock, UiPanel, UiSectionHeader } from '@/components/ui'
import { sanitizeForDisplay } from '@/lib/stackos/json'

defineProps<{
  authStatus: unknown
}>()
</script>

<template>
  <UiPanel v-if="authStatus" class="p-4">
    <UiSectionHeader
      title="Diagnostics"
      description="Sanitized daemon-side auth status for support and verification."
      as="h3"
    />
    <div class="mt-3">
      <UiJsonBlock
        :data="sanitizeForDisplay(authStatus)"
        density="compact"
        max-height="34rem"
        wrap
      />
    </div>
  </UiPanel>
</template>
