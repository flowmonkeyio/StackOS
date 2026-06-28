<script setup lang="ts">
import {
  UiBadge,
  UiButton,
  UiCallout,
  UiCard,
  UiMetadataStrip,
  UiSkeleton,
} from '@/components/ui'
import type { UiMetadataStripItem } from '@/components/ui/UiMetadataStrip.vue'

import HomeAgentHostStatus from './HomeAgentHostStatus.vue'
import type { HostStatusState } from './useHomeAgentHostStatuses'
import type { HealthState, SystemAction } from './useHomeSystemStatus'

defineProps<{
  health: HealthState
  isShell: boolean
  statusTone: 'success' | 'warning' | 'danger' | 'neutral'
  statusLabel: string
  systemFacts: UiMetadataStripItem[]
  systemBusy: SystemAction | null
  hostStatuses: HostStatusState
  hostStatusSummary: string
}>()

defineEmits<{
  restart: []
  doctor: []
  repair: []
  refreshHosts: []
}>()

const STATUS_DOT: Record<string, string> = {
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-danger',
  neutral: 'bg-fg-subtle',
}
</script>

<template>
  <UiCard
    section
    aria-label="System status"
  >
    <template #header>
      <div class="flex items-center gap-2.5">
        <span
          :class="['inline-block h-2 w-2 shrink-0 rounded-full', STATUS_DOT[statusTone]]"
          aria-hidden="true"
        />
        <h2 class="t-h3 text-fg-strong">
          Local service
        </h2>
        <UiBadge :tone="statusTone === 'neutral' ? 'neutral' : statusTone">
          {{ statusLabel }}
        </UiBadge>
      </div>
      <UiButton
        v-if="isShell"
        variant="secondary"
        size="sm"
        icon-left="refresh"
        :loading="systemBusy === 'restart'"
        @click="$emit('restart')"
      >
        Restart
      </UiButton>
    </template>

    <div
      v-if="health.kind === 'loading'"
      class="space-y-2"
    >
      <UiSkeleton
        shape="line"
        width="22rem"
      />
    </div>
    <UiCallout
      v-else-if="health.kind === 'down'"
      tone="danger"
      title="Can't reach the local service"
    >
      StackOS isn't responding on this machine.
      <template
        v-if="isShell"
        #actions
      >
        <UiButton
          variant="secondary"
          size="sm"
          :loading="systemBusy === 'restart'"
          @click="$emit('restart')"
        >
          Restart service
        </UiButton>
        <UiButton
          variant="secondary"
          size="sm"
          :loading="systemBusy === 'repair'"
          @click="$emit('repair')"
        >
          Install or repair
        </UiButton>
      </template>
    </UiCallout>
    <div
      v-else
      class="space-y-3"
    >
      <UiMetadataStrip
        :items="systemFacts"
        aria-label="System facts"
      />
      <HomeAgentHostStatus
        v-if="isShell"
        :state="hostStatuses"
        :summary="hostStatusSummary"
        @refresh="$emit('refreshHosts')"
      />
      <div
        v-if="isShell"
        class="flex flex-wrap gap-2"
      >
        <UiButton
          variant="secondary"
          size="sm"
          icon-left="shield-check"
          :loading="systemBusy === 'doctor'"
          @click="$emit('doctor')"
        >
          Run doctor
        </UiButton>
        <UiButton
          variant="secondary"
          size="sm"
          icon-left="wrench"
          :loading="systemBusy === 'repair'"
          @click="$emit('repair')"
        >
          Install or repair
        </UiButton>
      </div>
      <p
        v-else
        class="text-2xs text-fg-subtle"
      >
        Service controls live in the StackOS desktop app.
      </p>
    </div>
  </UiCard>
</template>
