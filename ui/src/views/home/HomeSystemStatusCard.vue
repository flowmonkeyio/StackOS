<script setup lang="ts">
import { computed } from 'vue'
import {
  UiBadge,
  UiButton,
  UiCallout,
  UiCard,
  UiMetadataStrip,
  UiSkeleton,
} from '@/components/ui'
import type { UiMetadataStripItem } from '@/components/ui/UiMetadataStrip.vue'
import { formatAbsoluteDateTime, parseStackOsDate } from '@/lib/stackos/time'

import HomeAgentHostStatus from './HomeAgentHostStatus.vue'
import type { HostStatusState } from './useHomeAgentHostStatuses'
import type { HomeSubscriptions } from './useHomeSubscriptions'
import type { HealthState, SystemAction } from './useHomeSystemStatus'

const props = defineProps<{
  health: HealthState
  isShell: boolean
  statusTone: 'success' | 'warning' | 'danger' | 'neutral'
  statusLabel: string
  systemFacts: UiMetadataStripItem[]
  systemBusy: SystemAction | null
  hostStatuses: HostStatusState
  hostStatusSummary: string
  subscriptions: HomeSubscriptions
}>()

const clock = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit', timeZoneName: 'short' })
const checkedTime = computed(() => {
  const checked = props.subscriptions.checked_at
  const ms = checked ? parseStackOsDate(checked).getTime() : NaN
  return Number.isFinite(ms) ? clock.format(ms) : null
})

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
        :disabled="!isShell"
        variant="secondary"
        size="sm"
        icon-left="refresh"
        :loading="systemBusy === 'restart'"
        @click="$emit('restart')"
      >
        Restart
      </UiButton>
    </template>

    <UiCallout
      v-if="health.kind === 'down'"
      tone="danger"
      title="Can't reach the local service"
    >
      StackOS isn't responding on this machine.
      <template #actions>
        <UiButton
          :disabled="!isShell"
          variant="secondary"
          size="sm"
          :loading="systemBusy === 'restart'"
          @click="$emit('restart')"
        >
          Restart service
        </UiButton>
        <UiButton
          :disabled="!isShell"
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
        v-if="health.kind === 'ok'"
        :items="systemFacts"
        aria-label="System facts"
      />
      <UiSkeleton v-else shape="block" height="38px" />
      <HomeAgentHostStatus
        :state="hostStatuses"
        :summary="hostStatusSummary"
        :subscriptions="subscriptions"
        @refresh="$emit('refreshHosts')"
      />
      <div
        class="flex flex-wrap items-center gap-2"
      >
        <UiButton
          :disabled="!isShell"
          variant="secondary"
          size="sm"
          icon-left="shield-check"
          :loading="systemBusy === 'doctor'"
          @click="$emit('doctor')"
        >
          Run doctor
        </UiButton>
        <UiButton
          :disabled="!isShell"
          variant="secondary"
          size="sm"
          icon-left="wrench"
          :loading="systemBusy === 'repair'"
          @click="$emit('repair')"
        >
          Install or repair
        </UiButton>
        <p v-if="checkedTime && subscriptions.checked_at" class="ml-auto text-2xs text-fg-muted">
          Checked <time :datetime="subscriptions.checked_at" :title="formatAbsoluteDateTime(subscriptions.checked_at)">{{ checkedTime }}</time>
        </p>
      </div>
      <p
        v-if="!isShell"
        class="text-2xs text-fg-subtle"
      >
        Service controls live in the StackOS desktop app.
      </p>
    </div>
  </UiCard>
</template>
