<script setup lang="ts">
import { computed } from 'vue'
import type { SchemaActiveTaskOut } from '@/api'
import AgentUsageSummary from '@/components/domain/AgentUsageSummary.vue'
import ActiveExecutionLine from '@/components/domain/ActiveExecutionLine.vue'
import { UiBadge, UiIcon, UiTooltip } from '@/components/ui'
import TaskPriorityBadge from '@/views/task-tracker/TaskPriorityBadge.vue'
import { isAutomaticDeliveryTask, taskDeliveryState } from '@/lib/task-tracker/delivery'
import { executionStatus } from '@/lib/task-tracker/execution'

const props = defineProps<{ task: SchemaActiveTaskOut; global?: boolean }>()
const automated = computed(() => isAutomaticDeliveryTask(props.task))
const delivery = computed(() => taskDeliveryState(props.task))
const state = computed(() => executionStatus(props.task.status, props.task.active_execution, delivery.value))
const subtitle = computed(() => delivery.value?.reason
  || (!props.task.active_execution && props.task.ticket_summary?.blocked_by_titles?.length
    ? `Blocked by: ${props.task.ticket_summary.blocked_by_titles.join(', ')}` : props.task.goal))
const completion = computed(() => {
  const summary = props.task.ticket_summary
  return summary?.status_counts ? `${summary.status_counts.complete ?? 0}/${summary.total_count}` : 'N/A'
})
const destination = computed(() => `/projects/${props.task.project_id}/tasks?view=graph&task=${encodeURIComponent(props.task.key)}`)
const fullText = computed(() => `${props.global ? `${props.task.project.name} · ` : ''}${props.task.title}${subtitle.value ? ` · ${subtitle.value}` : ''}`)
</script>

<template>
  <li class="active-task-row">
    <UiTooltip
      :content="fullText"
      class="active-task-row__tooltip"
    >
      <RouterLink
        :to="destination"
        class="active-task-row__link focus-ring-inset"
        :aria-label="`Open task graph: ${fullText}`"
        :title="fullText"
      >
        <span class="active-task-row__identity">
          <span
            v-if="global"
            class="active-task-row__project"
            :title="task.project.name"
          >{{ task.project.name }}</span>
          <span
            class="active-task-row__title"
            :title="task.title"
          >{{ task.title }}</span>
          <span
            class="active-task-row__goal"
            :class="{ 'text-warning-fg': delivery?.reason }"
            :title="subtitle"
          >{{ subtitle || '\u00a0' }}</span>
          <span class="active-task-row__facts">
            <TaskPriorityBadge :priority="task.priority_key" />
            <span class="active-task-row__automated">
              <UiBadge
                v-if="automated"
                tone="info"
              >Automated</UiBadge>
            </span>
            <span
              class="active-task-row__completion"
              :aria-label="`${completion} tickets complete`"
              :title="`${completion} tickets complete`"
            >
              <UiIcon
                name="tasks"
                :size="14"
                aria-hidden="true"
              />{{ completion }}
            </span>
            <UiBadge
              :tone="state.tone"
              dot
              variant="outline"
            >{{ state.label }}</UiBadge>
          </span>
        </span>
        <span class="active-task-row__usage">
          <AgentUsageSummary
            v-if="automated"
            :metrics="task.agent_stats"
            :active="!!task.active_execution"
            compact
          />
          <span
            v-else
            class="sr-only"
          >Manual task; automated usage is not applicable.</span>
        </span>
        <ActiveExecutionLine
          :item="task"
          :execution="task.active_execution"
          :last-execution="automated ? task.agent_stats?.last_execution : null"
          :owner="task.owner ?? 'Unassigned'"
          class="active-task-row__execution"
          focusable
        />
        <UiIcon
          class="active-task-row__arrow"
          name="chevron-right"
          :size="16"
          aria-hidden="true"
        />
      </RouterLink>
    </UiTooltip>
  </li>
</template>

<style scoped>
.active-task-row + .active-task-row { border-top: 1px solid var(--color-border-subtle); }
.active-task-row__tooltip { display: block; width: 100%; }
.active-task-row__tooltip > :deep(.ui-tooltip__bubble) { white-space: normal; width: max-content; max-width: min(32rem, calc(100vw - 32px)); overflow-wrap: anywhere; }
.active-task-row__tooltip:has(.active-execution-line:hover, .active-execution-line:focus) > :deep(.ui-tooltip__bubble) { display: none; }
.active-task-row__link { display: grid; grid-template-columns: minmax(0, 1fr) 240px 270px 16px; gap: 24px; align-items: center; min-height: 112px; padding: 12px 8px; color: var(--color-fg-default); text-decoration: none; }
.active-task-row__link:hover { background: var(--color-bg-surface-alt); }
.active-task-row__identity { min-width: 0; display: grid; grid-template-rows: 20px 20px 28px; align-content: center; }
.active-task-row__identity:has(.active-task-row__project) { grid-template-rows: 18px 20px 20px 28px; }
.active-task-row__project, .active-task-row__title, .active-task-row__goal { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.active-task-row__project { color: var(--color-fg-muted); font-size: 11px; }
.active-task-row__title { color: var(--color-fg-strong); font-size: 13px; font-weight: 600; }
.active-task-row__goal { font-size: 12px; color: var(--color-fg-muted); }
.active-task-row__goal.text-warning-fg { color: var(--color-warning-fg); }
.active-task-row__facts { display: grid; grid-template-columns: 36px 78px 84px minmax(0, 1fr); gap: 8px; align-items: center; white-space: nowrap; font-size: 11px; }
.active-task-row__facts > * { justify-self: start; min-width: 0; max-width: 100%; }
.active-task-row__automated { width: 78px; }
.active-task-row__completion { display: flex; gap: 4px; align-items: center; overflow: hidden; font-variant-numeric: tabular-nums; }
.active-task-row__usage { min-width: 0; min-height: 54px; }
.active-task-row__execution { min-width: 0; width: 100%; }
.active-task-row__execution :deep(.active-execution-line__activity) { min-height: 32px; grid-template-rows: 16px 16px; }
.active-task-row__arrow { color: var(--color-fg-subtle); }
@container active-tasks (max-width: 1050px) {
  .active-task-row__link { grid-template-columns: minmax(0, 1fr) 240px 16px; gap: 12px 20px; }
  .active-task-row__identity { grid-row: 1 / 3; }
  .active-task-row__usage { grid-column: 2; }
  .active-task-row__execution { grid-column: 2; grid-row: 2; }
  .active-task-row__arrow { grid-column: 3; grid-row: 1 / 3; }
}
@container active-tasks (max-width: 650px) {
  .active-task-row__link { grid-template-columns: minmax(0, 1fr) 16px; gap: 12px; padding: 16px 4px; }
  .active-task-row__identity { grid-row: 1; }
  .active-task-row__usage { grid-column: 1; grid-row: 2; width: min(100%, 300px); }
  .active-task-row__execution { grid-column: 1; grid-row: 3; width: min(100%, 340px); }
  .active-task-row__arrow { grid-column: 2; grid-row: 1 / 4; }
  .active-task-row__facts { grid-template-columns: 32px 72px 70px minmax(0, 1fr); gap: 4px; }
  .active-task-row__automated { width: 72px; }
  .active-task-row__facts :deep(.ui-badge) { padding-left: 4px; padding-right: 4px; font-size: 10px; }
}
@container active-tasks (max-width: 350px) {
  .active-task-row__facts { grid-template-columns: 32px 72px minmax(0, 1fr); grid-template-rows: 24px 24px; }
  .active-task-row__facts > :last-child { grid-column: 1 / -1; }
  .active-task-row__identity { grid-template-rows: 20px 20px 48px; }
  .active-task-row__identity:has(.active-task-row__project) { grid-template-rows: 18px 20px 20px 48px; }
}
</style>
