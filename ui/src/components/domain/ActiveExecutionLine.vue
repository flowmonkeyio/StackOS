<script setup lang="ts">
import { computed } from 'vue'
import type { ActiveExecution } from '@/lib/task-tracker/types'
import type { SchemaExecutionIdentityOut } from '@/api'
import { activeExecutionTitle, executionRole } from '@/lib/task-tracker/execution'
import { resolveAgentHostLogo, resolveAgentOwnerLogo } from '@/lib/stackos/agentIdentity'
import { UiIcon } from '@/components/ui'

const props = defineProps<{ execution?: ActiveExecution | null; lastExecution?: SchemaExecutionIdentityOut | null; owner?: string | null; detailed?: boolean; focusable?: boolean }>()
const identity = computed(() => props.execution ?? props.lastExecution)
const agent = computed(() => identity.value ? executionRole(identity.value.agent) : '')
const logo = computed(() => identity.value ? resolveAgentHostLogo(identity.value.host_key) : resolveAgentOwnerLogo(props.owner))
const start = computed(() => identity.value ? new Date(identity.value.started_at) : null)
const time = computed(() => start.value?.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) ?? '')
const full = computed(() => identity.value ? activeExecutionTitle(identity.value) : `Assigned: ${props.owner ?? 'Unassigned'}`)
</script>

<template>
  <span
    v-if="identity || owner"
    class="active-execution-line focus-ring"
    :class="{ 'active-execution-line--detailed': detailed, 'active-execution-line--last': !execution && identity }"
    :title="full"
    :aria-label="identity ? `${execution ? 'Active now' : 'Last'}: ${full}` : full"
    :tabindex="focusable ? 0 : undefined"
    :data-active-execution="execution ? '' : undefined"
    :data-last-execution="!execution && identity ? '' : undefined"
  >
    <img
      v-if="logo"
      :src="logo"
      :alt="identity?.host_key ?? owner ?? ''"
      class="active-execution-line__logo"
    >
    <UiIcon
      v-else
      name="user"
      class="active-execution-line__logo"
    />
    <span class="active-execution-line__identity">
      <span class="active-execution-line__agent">{{ identity ? `${execution ? '' : 'Last: '}${agent}` : `Assigned: ${owner}` }}</span>
      <span
        v-if="identity"
        class="active-execution-line__model"
      >{{ identity.model ?? 'Model not recorded' }}</span>
    </span>
    <time
      v-if="execution"
      :datetime="execution.started_at"
    >{{ detailed ? start?.toLocaleString() : time }}</time>
  </span>
</template>

<style scoped>
.active-execution-line { display: flex; min-width: 0; max-width: 100%; align-items: center; gap: 8px; color: var(--color-fg-default); font-size: 11px; line-height: 16px; white-space: nowrap; }
.active-execution-line__logo { width: 22px; height: 22px; object-fit: contain; flex: none; }
.active-execution-line__identity { flex: 1; min-width: 0; display: grid; }
.active-execution-line__agent, .active-execution-line__model { overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.active-execution-line__model { color: var(--color-fg-muted); }
.active-execution-line time { flex: none; font-variant-numeric: tabular-nums; }
.active-execution-line--detailed { flex-wrap: wrap; white-space: normal; font-size: 12px; }
.active-execution-line--detailed .active-execution-line__agent,
.active-execution-line--detailed .active-execution-line__model { flex: 0 1 auto; max-width: 100%; overflow: visible; }
</style>
