<script setup lang="ts">
// ProjectSwitcher — read-only project navigation dropdown.
//
// Behavior:
//   - Displays the current route project name + chevron in collapsed state
//   - Click to open dropdown with list of all projects (archived last)
//   - Selecting a project only navigates to `/projects/{id}/overview`

import { computed, ref, onBeforeUnmount, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute, useRouter } from 'vue-router'

import { useProjectsStore } from '@/stores/projects'

const projects = useProjectsStore()
const router = useRouter()
const route = useRoute()
const { items, activeProject } = storeToRefs(projects)

const open = ref(false)
const rootEl = ref<HTMLDivElement | null>(null)

function toggle(): void {
  open.value = !open.value
}
function close(): void {
  open.value = false
}

const sortedItems = computed(() => {
  const live = [...items.value].filter((p) => p.is_active)
  const archived = [...items.value].filter((p) => !p.is_active)
  return [...live, ...archived]
})

const routeProjectId = computed(() => {
  const raw = route.params.id
  const value = Array.isArray(raw) ? raw[0] : raw
  const parsed = Number.parseInt(String(value ?? ''), 10)
  return Number.isNaN(parsed) ? null : parsed
})

const selectedProject = computed(() => {
  if (routeProjectId.value !== null) {
    return items.value.find((project) => project.id === routeProjectId.value) ?? activeProject.value
  }
  return activeProject.value
})

async function pick(id: number): Promise<void> {
  close()
  await router.push(`/projects/${id}/overview`)
}

function onClickOutside(e: MouseEvent): void {
  if (!rootEl.value) return
  if (!rootEl.value.contains(e.target as Node)) close()
}

onMounted(() => document.addEventListener('mousedown', onClickOutside))
onBeforeUnmount(() => document.removeEventListener('mousedown', onClickOutside))
</script>

<template>
  <div
    ref="rootEl"
    class="relative"
  >
    <button
      type="button"
      class="flex w-full items-center justify-between gap-2 rounded-sm border border-default bg-bg-surface px-3 py-2 text-sm shadow-xs hover:bg-bg-surface-alt focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
      :aria-expanded="open"
      aria-haspopup="listbox"
      @click="toggle"
    >
      <span class="truncate text-left">
        <span
          v-if="selectedProject"
          class="font-medium text-fg-strong"
        >
          {{ selectedProject.name }}
        </span>
        <span
          v-else
          class="text-fg-muted"
        >No project selected</span>
      </span>
      <span
        aria-hidden="true"
        class="text-xs text-fg-muted"
      >▾</span>
    </button>
    <div
      v-if="open"
      role="listbox"
      class="absolute z-dropdown mt-1 max-h-72 w-full overflow-y-auto rounded-md border border-default bg-bg-surface py-1 shadow-md"
    >
      <button
        v-for="p in sortedItems"
        :key="p.id"
        role="option"
        :aria-selected="p.id === selectedProject?.id"
        type="button"
        class="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-bg-surface-alt focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
        :class="p.id === selectedProject?.id ? 'bg-accent-subtle' : ''"
        @click="pick(p.id)"
      >
        <span>
          <span class="block truncate font-medium text-fg-strong">
            {{ p.name }}
          </span>
          <span class="block truncate text-xs text-fg-muted">
            {{ p.slug }} · {{ p.domain }}
          </span>
        </span>
        <span
          v-if="!p.is_active"
          class="rounded-xs bg-neutral-subtle px-1.5 py-0.5 text-[10px] font-medium text-neutral-fg"
        >
          archived
        </span>
      </button>
      <div
        v-if="sortedItems.length === 0"
        class="px-3 py-2 text-sm text-fg-muted"
      >
        No projects yet.
      </div>
    </div>
  </div>
</template>
