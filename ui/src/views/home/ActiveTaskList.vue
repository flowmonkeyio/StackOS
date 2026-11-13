<script setup lang="ts">
import { UiCallout, UiSectionHeader, UiSkeleton } from '@/components/ui'
import { useActiveTasks } from '@/composables/useActiveTasks'
import ActiveTaskRow from './ActiveTaskRow.vue'

const props = withDefaults(defineProps<{ projectId?: number | null }>(), { projectId: null })
const { items, loading, error, asOf, feed, refresh } = useActiveTasks(props.projectId)
defineExpose({ refresh })
</script>

<template>
  <section
    class="active-task-list"
    aria-label="Active tasks"
    :aria-busy="loading"
  >
    <UiSectionHeader title="Active tasks">
      <template #actions>
        <span
          role="status"
          class="text-xs text-fg-muted"
        >
          {{ error ? 'Refresh failed' : feed.status === 'reconnecting' ? 'Updates reconnecting' : feed.status === 'connecting' ? 'Connecting to updates…' : loading ? 'Refreshing…' : '' }}
        </span>
        <span
          v-if="asOf"
          class="text-xs tabular-nums text-fg-muted"
        >{{ items.length }} {{ items.length === 1 ? 'task' : 'tasks' }}</span>
      </template>
    </UiSectionHeader>
    <UiCallout
      v-if="error"
      tone="warning"
      class="mt-3"
    >
      {{ error }}<span v-if="asOf"> Previously loaded tasks are shown.</span>
    </UiCallout>
    <UiSkeleton
      v-if="loading && !asOf"
      class="mt-3"
      height="7rem"
      shape="block"
      aria-label="Loading active tasks"
    />
    <ul
      v-else-if="items.length"
      class="mt-3 border-y border-subtle"
    >
      <ActiveTaskRow
        v-for="task in items"
        :key="`${task.project_id}:${task.key}`"
        :task="task"
        :global="projectId === null"
      />
    </ul>
    <p
      v-else-if="!error"
      class="mt-3 py-5 text-sm text-fg-muted"
    >
      No tasks are in progress{{ projectId === null ? '' : ' in this project' }}.
    </p>
  </section>
</template>

<style scoped>
.active-task-list { min-width: 0; container: active-tasks / inline-size; }
</style>
