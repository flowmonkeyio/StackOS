<script setup lang="ts">
import { computed } from 'vue'
import { UiBadge, UiButton } from '@/components/ui'
import { resolveStatus } from '@/design/status'
import type { IntegrationAvailability } from './viewModel'

const props = defineProps<{
  availability?: IntegrationAvailability
  loading: boolean
  connectionsHref: string
}>()

const state = computed(() => props.availability && resolveStatus('integration', props.availability.state))
</script>

<template>
  <div class="mt-3 space-y-2 text-xs" aria-label="Integration availability">
    <p v-if="loading" class="text-fg-muted">Checking availability…</p>
    <template v-else-if="availability && state">
      <UiBadge :tone="state.tone" size="sm">{{ state.label }}</UiBadge>
      <p class="text-fg-muted">
        {{ availability.executable_action_count }} of {{ availability.total_action_count }} actions executable
        <span v-if="availability.hidden_action_count"> · {{ availability.hidden_action_count }} hidden from agent discovery</span>
      </p>
      <p v-if="availability.next_action?.reason" class="text-fg-muted">
        {{ availability.next_action.reason }}
      </p>
      <UiButton
        v-if="availability.state === 'repair_required'"
        :href="connectionsHref"
        size="sm"
        variant="link"
      >Review connections</UiButton>
    </template>
    <p v-else class="text-fg-muted">Availability unavailable</p>
  </div>
</template>
