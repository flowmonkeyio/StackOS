<script setup lang="ts">
import { UiBadge, UiCallout, UiCard, UiEmptyState, UiIcon, UiSkeleton } from '@/components/ui'
import type { Project } from '@/stores/projects'

defineProps<{
  items: Project[]
  loading: boolean
  error: string | null
}>()
</script>

<template>
  <section
    aria-label="Projects"
    class="space-y-3"
  >
    <h2 class="t-h3 text-fg-strong">
      Projects
    </h2>

    <UiCallout
      v-if="error"
      tone="danger"
    >
      {{ error }}
    </UiCallout>

    <div
      v-if="loading && items.length === 0"
      class="grid gap-4 sm:grid-cols-2 xl:grid-cols-3"
    >
      <UiSkeleton
        v-for="n in 3"
        :key="n"
        shape="block"
        height="6rem"
      />
    </div>

    <UiCard
      v-else-if="items.length === 0"
      section
    >
      <UiEmptyState
        icon="cube"
        title="No projects yet"
        description="A project is created when an agent binds this workspace to StackOS."
      />
    </UiCard>

    <ul
      v-else
      class="grid gap-4 sm:grid-cols-2 xl:grid-cols-3"
    >
      <li
        v-for="project in items"
        :key="project.id"
      >
        <RouterLink
          :to="`/projects/${project.id}`"
          class="focus-ring group flex h-full flex-col rounded-lg border border-default bg-bg-surface p-4 shadow-xs transition-colors duration-fast hover:border-strong hover:bg-bg-surface-alt"
        >
          <div class="flex items-start justify-between gap-2">
            <span class="min-w-0 truncate text-sm font-semibold text-fg-strong">
              {{ project.name }}
            </span>
            <UiBadge
              v-if="!project.is_active"
              tone="neutral"
              size="sm"
            >
              Archived
            </UiBadge>
          </div>
          <span class="mt-1 truncate font-mono text-2xs text-fg-subtle">{{ project.slug }}</span>
          <div class="mt-3 flex items-center justify-between gap-2">
            <span class="truncate text-2xs text-fg-muted">{{ project.domain || '-' }}</span>
            <span class="inline-flex items-center gap-1 text-2xs font-medium text-fg-link">
              Open
              <UiIcon
                name="arrow-right"
                class="h-3.5 w-3.5 opacity-0 transition-opacity duration-fast group-hover:opacity-100"
                aria-hidden="true"
              />
            </span>
          </div>
        </RouterLink>
      </li>
    </ul>
  </section>
</template>
