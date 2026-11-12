<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ActiveExecution } from '@/lib/task-tracker/types'
import type { SchemaExecutionIdentityOut, SchemaWorkDurationOut } from '@/api'
import { activeExecutionTitle, executionRole } from '@/lib/task-tracker/execution'
import { resolveAgentHostLogo, resolveAgentOwnerLogo } from '@/lib/stackos/agentIdentity'
import { UiIcon, UiTooltip } from '@/components/ui'
import UsageLiveIndicator from './UsageLiveIndicator.vue'
import WorkTime from './WorkTime.vue'

const props = defineProps<{ item?: { status: string; work_duration?: SchemaWorkDurationOut | null; started_at?: string | null; completed_at?: string | null } | null; execution?: ActiveExecution | null; lastExecution?: SchemaExecutionIdentityOut | null; owner?: string | null; detailed?: boolean; focusable?: boolean }>()
const identity = computed(() => props.execution ?? props.lastExecution)
const agent = computed(() => identity.value ? executionRole(identity.value.agent) : '')
const logo = computed(() => identity.value ? resolveAgentHostLogo(identity.value.host_key) : resolveAgentOwnerLogo(props.owner))
const workTime = ref<InstanceType<typeof WorkTime> | null>(null)
const full = computed(() => identity.value ? activeExecutionTitle(identity.value) : `Assigned: ${props.owner ?? 'Unassigned'}`)
</script>

<template>
  <UiTooltip
    v-if="item || identity || owner"
    :content="full"
    :disabled="!focusable"
    class="active-execution-line focus-ring"
    :class="{ 'active-execution-line--detailed': detailed, 'active-execution-line--last': !execution && identity }"
    :title="full"
    :aria-label="`${identity ? `${execution ? 'Active now' : 'Last'}: ${full}` : full}${item && workTime ? ` · ${workTime.label}` : ''}`"
    :tabindex="focusable ? 0 : undefined"
    :data-active-execution="execution ? '' : undefined"
    :data-last-execution="!execution && identity ? '' : undefined"
    @focusin.stop
    @focusout.stop
  >
    <img
      v-if="logo"
      :src="logo"
      :alt="identity?.host_key ?? owner ?? ''"
      class="active-execution-line__logo"
    >
    <UiIcon
      v-else-if="identity || owner"
      name="user"
      class="active-execution-line__logo"
    />
    <span
      v-if="identity || owner"
      class="active-execution-line__identity"
    >
      <span class="active-execution-line__agent">{{ identity ? `${execution ? '' : 'Last: '}${agent}` : `Assigned: ${owner}` }}</span>
      <span
        v-if="identity"
        class="active-execution-line__model"
      >{{ identity.model ?? 'Model not recorded' }}</span>
    </span>
    <span
      v-if="item || execution"
      class="active-execution-line__activity"
    >
      <WorkTime
        v-if="item"
        ref="workTime"
        :item="item"
        :execution="execution"
      />
      <UsageLiveIndicator
        v-if="execution"
        :active="true"
      />
    </span>
  </UiTooltip>
</template>

<style scoped>
.active-execution-line { display: flex; min-width: 0; max-width: 100%; align-items: center; gap: 8px; color: var(--color-fg-default); font-size: 11px; line-height: 16px; white-space: nowrap; }
.active-execution-line :deep(.ui-tooltip__bubble) { left: auto; right: 0; transform: none; width: max-content; max-width: min(32rem, 100%); white-space: normal; overflow-wrap: anywhere; }
.active-execution-line__logo { width: 32px; height: 32px; object-fit: contain; flex: none; }
img.active-execution-line__logo { border-radius: var(--radius-sm); box-shadow: var(--shadow-sm); }
.active-execution-line__identity { flex: 1; min-width: 0; display: grid; }
.active-execution-line__agent, .active-execution-line__model { overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.active-execution-line__model { color: var(--color-fg-muted); }
.active-execution-line__activity { flex: none; display: grid; justify-items: end; min-width: 80px; margin-left: auto; }
.active-execution-line--detailed .active-execution-line__activity { display: flex; align-items: center; gap: 12px; }
.active-execution-line--detailed { flex-wrap: wrap; white-space: normal; font-size: 12px; }
.active-execution-line--detailed .active-execution-line__agent,
.active-execution-line--detailed .active-execution-line__model { flex: 0 1 auto; max-width: 100%; overflow: visible; }
</style>
