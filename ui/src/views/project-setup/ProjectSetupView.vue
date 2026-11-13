<script setup lang="ts">
import { computed } from 'vue'
import { ProjectPageHeader } from '@/components/domain'
import { UiButton, UiCard, UiPageShell } from '@/components/ui'
import { useProjectRouteScope } from '@/composables/useProjectRouteScope'
import { useProjectSetup } from './useProjectSetup'

const { projectId } = useProjectRouteScope()
const { state, attempt, error, running, refresh } = useProjectSetup(projectId)
const heading = computed(() => {
  if (state.value?.folder_status === 'unavailable') return 'Project folder unavailable'
  if (attempt.value?.status === 'queued') return 'Setup is queued'
  if (attempt.value?.status === 'running') return 'Your agent is preparing the project'
  if (attempt.value?.status === 'needs_input') return 'Your agent needs your input'
  if (attempt.value?.status === 'success') return 'Setup completed'
  if (attempt.value) return `Setup ${attempt.value.status}`
  return 'Project setup'
})
</script>

<template>
  <UiPageShell>
    <ProjectPageHeader
      :project-id="projectId"
      title="Project setup"
      :breadcrumbs="[{ label: 'Project setup' }]"
      description="Setup progress and project workflow guidance."
    >
      <template #actions>
        <RouterLink
          :to="`/projects/${projectId}`"
          class="focus-ring text-sm text-fg-link"
        >
          Back to Home
        </RouterLink>
        <UiButton
          size="sm"
          :loading="running"
          @click="refresh"
        >
          Refresh
        </UiButton>
      </template>
    </ProjectPageHeader>
    <p
      v-if="error"
      role="alert"
      class="break-words text-danger-fg"
    >
      {{ error }}
    </p>
    <UiCard v-if="state?.eligible">
      <template #header>
        <h2 class="t-h3">
          {{ heading }}
        </h2>
      </template>
      <div
        class="space-y-3 break-words"
        aria-live="polite"
      >
        <p
          v-if="state.folder_status === 'unavailable'"
          class="text-fg-muted"
        >
          Restore access to the project's bound folder, then refresh. Your setup history and reminder choices are retained.
        </p>
        <p
          v-if="state.attempt_diagnostic"
          class="text-fg-muted"
        >
          {{ state.attempt_diagnostic }}
        </p>
        <template v-if="attempt">
          <p
            v-if="attempt.status === 'queued'"
            class="text-fg-muted"
          >
            Waiting for available agent capacity. This attempt will start when a slot is free.
          </p>
          <p
            v-if="['queued', 'running'].includes(attempt.status)"
            class="text-xs text-fg-muted"
          >
            You can leave this page. Closing it does not stop setup.
          </p>
          <p v-if="attempt.progress?.message">
            {{ attempt.progress.message }}
          </p>
          <p v-if="attempt.question?.answer_state === 'pending'">
            {{ attempt.question.prompt }}
          </p>
          <p
            v-if="attempt.question?.response_channel === 'existing_agent'"
            class="text-fg-muted"
          >
            Answer in the agent session where you started setup.
          </p>
          <p
            v-if="attempt.recovery_condition"
            class="text-fg-muted"
          >
            {{ attempt.recovery_condition }}
          </p>
          <p
            v-if="attempt.partial_changes_possible"
            class="text-fg-muted"
          >
            Some files may have changed. The agent must check them before continuing.
          </p>
        </template>
        <p
          v-else
          class="text-fg-muted"
        >
          No setup attempt is recorded. Return to Home to set up or update this project.
        </p>
      </div>
      <!-- The panel ticket extends this retained attempt shell with answers,
           workflow results and recovery through the existing setup operations. -->
    </UiCard>
    <p
      v-else-if="state"
      class="text-fg-muted"
    >
      Repository setup is available for projects with a bound folder.
    </p>
    <p
      v-else-if="!error"
      role="status"
      class="text-fg-muted"
    >
      Loading project setup…
    </p>
  </UiPageShell>
</template>
