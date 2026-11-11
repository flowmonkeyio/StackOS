<script setup lang="ts">
// ProjectSwitcher emits navigation/management intents; shared owners persist them.

import { computed, ref, onBeforeUnmount, onMounted, useId } from 'vue'

import UiIcon from '@/components/ui/UiIcon.vue'
import UiDropdownMenu, { type DropdownItem } from '@/components/ui/UiDropdownMenu.vue'
import UiReorderList from '@/components/ui/UiReorderList.vue'
import UiReorderHandle from '@/components/ui/UiReorderHandle.vue'
import type { ReorderHandleBindings, ReorderMove } from '@/components/ui/verticalReorder'
import type { Project } from '@/stores/projects'

const props = defineProps<{
  items: Project[]
  selectedProject: Project | null
  loading?: boolean
  managementPending?: boolean
  managementOpen?: boolean
  orderReady?: boolean
  error?: string | null
}>()
const emit = defineEmits<{
  select: [projectId: number]
  opened: []
  rename: [projectId: number]
  delete: [projectId: number]
  move: [move: ReorderMove]
}>()

const open = ref(false)
const menuId = useId()
const rootEl = ref<HTMLDivElement | null>(null)
const menuPanel = ref<HTMLElement>()
const menuStyle = ref<Record<string, string>>({})
const liveItems = computed(() => props.items.filter((p) => p.is_active))
const archivedItems = computed(() => props.items.filter((p) => !p.is_active))
const disabled = computed(() => props.loading || props.managementPending)

function positionMenu(): void {
  if (!open.value || !rootEl.value) return
  const bounds = rootEl.value.getBoundingClientRect()
  const left = Math.max(12, Math.min(bounds.left, window.innerWidth - 372))
  menuStyle.value = {
    left: `${left - bounds.left}px`,
    maxWidth: `${Math.max(0, window.innerWidth - left - 12)}px`,
    maxHeight: `${Math.max(0, Math.min(288, window.innerHeight - bounds.bottom - 18))}px`,
  }
}

function toggle(): void {
  open.value = !open.value
  if (open.value) {
    positionMenu()
    emit('opened')
  }
}
function close(): void {
  open.value = false
}

function actions(project: Project, handle?: ReorderHandleBindings): DropdownItem[] {
  const moves = handle ? ['top', 'up', 'down'].map((key) => handle.items.find((item) => item.key === key)!) : []
  return [
    { key: 'rename', label: 'Rename project…', icon: 'edit', disabled: disabled.value, onSelect: () => emit('rename', project.id) },
    ...(moves.length ? [{ key: 'move-divider', as: 'separator' as const }, ...moves] : []),
    { key: 'delete-divider', as: 'separator' },
    { key: 'delete', label: 'Delete project…', icon: 'trash', tone: 'danger', disabled: disabled.value, onSelect: () => emit('delete', project.id) },
  ]
}

function projectInitials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('')
}

function pick(id: number): void {
  close()
  emit('select', id)
}

function onClickOutside(e: MouseEvent): void {
  if (!rootEl.value || props.managementOpen) return
  if (!rootEl.value.contains(e.target as Node)) close()
}

function onKeydown(e: KeyboardEvent): void {
  // Nested menus and active gestures own their first Escape.
  if (e.key === 'Escape' && !e.defaultPrevented && !menuPanel.value?.querySelector('[role="menu"]')) close()
}

onMounted(() => {
  document.addEventListener('mousedown', onClickOutside)
  window.addEventListener('resize', positionMenu)
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onClickOutside)
  window.removeEventListener('resize', positionMenu)
})
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
      :aria-controls="open ? menuId : undefined"
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
      <span class="sr-only">Switch project</span>
      <UiIcon
        name="chevron-up-down"
        class="h-4 w-4 shrink-0 text-sb-muted"
        aria-hidden="true"
      />
    </button>
    <div
      v-if="open"
      :id="menuId"
      ref="menuPanel"
      role="region"
      aria-label="Projects"
      class="absolute left-0 z-dropdown mt-1.5 flex w-[360px] flex-col rounded-lg border border-sb-border bg-sb-elevated p-1 shadow-lg"
      :style="menuStyle"
    >
      <div
        v-if="loading"
        class="px-2 py-1 text-xs text-sb-muted"
        role="status"
      >
        Loading projects…
      </div>
      <div
        v-if="managementPending"
        class="px-2 py-1 text-xs text-sb-muted"
        role="status"
      >
        Saving…
      </div>
      <div
        v-if="error"
        class="px-2 py-1 text-xs text-danger-fg"
        role="alert"
      >
        {{ error }}
        <button
          type="button"
          class="focus-ring-sb underline"
          :disabled="disabled"
          @click="emit('opened')"
        >
          Try again
        </button>
      </div>
      <UiReorderList
        :items="liveItems"
        :item-key="(p: Project) => String(p.id)"
        :item-label="(p: Project) => p.name"
        :item-version="(p: Project) => String(p.is_active)"
        scope-key="project-catalog"
        :disabled="disabled || !orderReady"
        class="min-h-0 overflow-x-hidden overflow-y-auto"
        style="--reorder-gap: 0px"
        @move="emit('move', $event)"
      >
        <template #default="{ item: p, handle }">
          <div
            class="flex min-w-0 items-center gap-2 rounded-sm px-2 hover:bg-sb-hover"
            :class="p.id === selectedProject?.id ? 'bg-sb-active' : ''"
          >
            <UiReorderHandle
              v-bind="handle"
              :portal-target="menuPanel"
              class="text-sb-muted"
            />
            <button
              type="button"
              class="focus-ring-sb flex min-w-0 flex-1 items-center gap-2 overflow-hidden rounded-sm py-1.5 text-left text-sm"
              :aria-current="p.id === selectedProject?.id ? 'true' : undefined"
              :data-project-select="p.id"
              @click="pick(p.id)"
            >
              <span class="min-w-0 flex-1">
                <span
                  class="block truncate font-medium text-sb-strong"
                  :title="p.name"
                >{{ p.name }}</span>
                <span class="block truncate text-2xs text-sb-muted">{{ p.slug }} · {{ p.domain }}</span>
              </span>
              <UiIcon
                v-if="p.id === selectedProject?.id"
                name="check"
                class="h-3.5 w-3.5 shrink-0 text-sb-accent"
                aria-hidden="true"
              />
            </button>
            <UiDropdownMenu
              :items="actions(p, handle)"
              :portal-target="menuPanel"
              placement="bottom-end"
              :aria-label="`Actions for ${p.name}`"
            >
              <template #trigger="{ toggle: toggleActions, open: actionsOpen }">
                <button
                  type="button"
                  data-dropdown-trigger
                  class="focus-ring-sb flex h-7 w-7 shrink-0 items-center justify-center rounded-sm text-sb-muted hover:bg-sb-active"
                  :aria-label="`Actions for ${p.name}`"
                  :aria-expanded="actionsOpen"
                  aria-haspopup="menu"
                  :disabled="disabled"
                  @click="toggleActions"
                >
                  <UiIcon
                    name="dots-vertical"
                    class="h-4 w-4"
                    aria-hidden="true"
                  />
                </button>
              </template>
            </UiDropdownMenu>
          </div>
        </template>
      </UiReorderList>
      <div
        v-if="archivedItems.length"
        class="min-h-0 overflow-x-hidden overflow-y-auto"
      >
        <div
          v-for="p in archivedItems"
          :key="p.id"
          class="flex min-w-0 items-center gap-2 px-2"
        >
          <button
            type="button"
            class="focus-ring-sb min-w-0 flex-1 overflow-hidden rounded-sm py-1.5 text-left text-sm"
            :data-project-select="p.id"
            :aria-current="p.id === selectedProject?.id ? 'true' : undefined"
            @click="pick(p.id)"
          >
            <span class="block truncate font-medium text-sb-strong">{{ p.name }}</span>
            <span class="block truncate text-2xs text-sb-muted">{{ p.slug }} · {{ p.domain }} · archived</span>
          </button>
          <UiDropdownMenu
            :items="actions(p)"
            :portal-target="menuPanel"
            placement="bottom-end"
            :aria-label="`Actions for ${p.name}`"
          >
            <template #trigger="{ toggle: toggleActions, open: actionsOpen }">
              <button
                type="button"
                data-dropdown-trigger
                class="focus-ring-sb flex h-7 w-7 shrink-0 items-center justify-center rounded-sm text-sb-muted"
                :aria-label="`Actions for ${p.name}`"
                :aria-expanded="actionsOpen"
                aria-haspopup="menu"
                :disabled="disabled"
                @click="toggleActions"
              >
                <UiIcon
                  name="dots-vertical"
                  class="h-4 w-4"
                />
              </button>
            </template>
          </UiDropdownMenu>
        </div>
      </div>
      <div
        v-if="items.length === 0 && !loading"
        class="px-2 py-1.5 text-sm text-sb-muted"
      >
        No projects yet.
      </div>
    </div>
  </div>
</template>
