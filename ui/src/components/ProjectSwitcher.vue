<script setup lang="ts">
// ProjectSwitcher — read-only project navigation dropdown.
//
// Rendered against the dark sidebar material (`sb-*` tokens).
//
// Behavior:
//   - Displays the current route project name + chevron in collapsed state
//   - Click to open dropdown with list of all projects (archived last)
//   - Selecting a project only navigates to `/projects/{id}/overview`

import { computed, ref, onBeforeUnmount, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute, useRouter } from 'vue-router'

import UiIcon from '@/components/ui/UiIcon.vue'
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

function projectInitials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('')
}

async function pick(id: number): Promise<void> {
  close()
  await router.push(`/projects/${id}/overview`)
}

function onClickOutside(e: MouseEvent): void {
  if (!rootEl.value) return
  if (!rootEl.value.contains(e.target as Node)) close()
}

function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape') close()
}

onMounted(() => document.addEventListener('mousedown', onClickOutside))
onBeforeUnmount(() => document.removeEventListener('mousedown', onClickOutside))
</script>

<template>
  <div
    ref="rootEl"
    class="relative min-w-0"
    @keydown="onKeydown"
  >
    <button
      type="button"
      class="focus-ring-sb flex w-full min-w-0 items-center gap-2.5 rounded-md border border-sb-border bg-sb-hover px-2.5 py-2 text-left text-sm transition-colors duration-fast hover:bg-sb-active"
      :aria-expanded="open"
      aria-haspopup="listbox"
      aria-label="Switch project"
      @click="toggle"
    >
      <span
        class="brand-gradient flex h-7 w-7 shrink-0 items-center justify-center rounded-sm text-2xs font-bold text-white"
        aria-hidden="true"
      >
        {{ selectedProject ? projectInitials(selectedProject.name) : '—' }}
      </span>
      <span class="min-w-0 flex-1">
        <span
          v-if="selectedProject"
          class="block truncate font-medium leading-tight text-sb-strong"
        >
          {{ selectedProject.name }}
        </span>
        <span
          v-else
          class="block truncate font-medium leading-tight text-sb-muted"
        >No project selected</span>
        <span
          v-if="selectedProject"
          class="block truncate text-2xs leading-tight text-sb-muted"
        >
          {{ selectedProject.slug }}
        </span>
      </span>
      <UiIcon
        name="chevron-up-down"
        class="h-4 w-4 shrink-0 text-sb-muted"
        aria-hidden="true"
      />
    </button>
    <div
      v-if="open"
      role="listbox"
      aria-label="Projects"
      class="absolute z-dropdown mt-1.5 max-h-72 w-full overflow-x-hidden overflow-y-auto rounded-lg border border-sb-border bg-sb-elevated p-1 shadow-lg"
    >
      <button
        v-for="p in sortedItems"
        :key="p.id"
        role="option"
        :aria-selected="p.id === selectedProject?.id"
        type="button"
        class="focus-ring-sb flex w-full min-w-0 items-center gap-2 overflow-hidden rounded-sm px-2 py-1.5 text-left text-sm transition-colors duration-fast hover:bg-sb-hover"
        :class="p.id === selectedProject?.id ? 'bg-sb-active' : ''"
        @click="pick(p.id)"
      >
        <span class="min-w-0 flex-1">
          <span class="block truncate font-medium text-sb-strong">
            {{ p.name }}
          </span>
          <span class="block truncate text-2xs text-sb-muted">
            {{ p.slug }} · {{ p.domain }}
          </span>
        </span>
        <UiIcon
          v-if="p.id === selectedProject?.id"
          name="check"
          class="h-3.5 w-3.5 shrink-0 text-sb-accent"
          aria-hidden="true"
        />
        <span
          v-if="!p.is_active"
          class="shrink-0 rounded-full border border-sb-border px-1.5 py-0.5 text-2xs font-medium text-sb-muted"
        >
          archived
        </span>
      </button>
      <div
        v-if="sortedItems.length === 0"
        class="px-2 py-1.5 text-sm text-sb-muted"
      >
        No projects yet.
      </div>
    </div>
  </div>
</template>
