<script setup lang="ts">
// App shell — dark sidebar + main content + toast region.
//
// The sidebar is the identity anchor: dark in both themes (`sb-*` tokens),
// holding the brand, ProjectSwitcher, primary nav, and theme toggle.
// At < md it becomes a slide-over drawer under a scrim.

import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { RouterView, useRoute, useRouter } from 'vue-router'

import ProjectSwitcher from '@/components/ProjectSwitcher.vue'
import PluginNavRenderer from '@/components/renderers/PluginNavRenderer.vue'
import UiIcon from '@/components/ui/UiIcon.vue'
import {
  projectNavSections as buildProjectNavSections,
  type StackOsNavSection,
} from '@/lib/stackos/nav'
import { useAuthStore } from '@/stores/auth'
import { useStackOsCatalogStore } from '@/stores/plugins'
import { useProjectsStore } from '@/stores/projects'
import { useToastsStore } from '@/stores/toasts'

const auth = useAuthStore()
const projects = useProjectsStore()
const catalog = useStackOsCatalogStore()
const toasts = useToastsStore()
const route = useRoute()
const router = useRouter()

const { activeProject } = storeToRefs(projects)
const { enabledPlugins } = storeToRefs(catalog)
const { items: toastItems } = storeToRefs(toasts)

const theme = ref<'light' | 'dark'>('light')
const drawerOpen = ref(false)
const lastCatalogProjectId = ref<number | null>(null)

function parseProjectId(raw: unknown): number | null {
  const value = Array.isArray(raw) ? raw[0] : raw
  const parsed = Number.parseInt(String(value ?? ''), 10)
  return Number.isNaN(parsed) ? null : parsed
}

const routeProjectId = computed(() => parseProjectId(route.params.id))

const currentProjectId = computed(() => routeProjectId.value ?? activeProject.value?.id ?? null)

const currentProject = computed(() => {
  if (currentProjectId.value === null) return activeProject.value
  return projects.getById(currentProjectId.value) ?? activeProject.value
})

function applyTheme(): void {
  if (typeof document === 'undefined') return
  document.documentElement.classList.toggle('dark', theme.value === 'dark')
  document.documentElement.dataset.theme = theme.value
  document.documentElement.style.colorScheme = theme.value
}

function toggleTheme(): void {
  theme.value = theme.value === 'dark' ? 'light' : 'dark'
  applyTheme()
  try {
    localStorage.setItem('cs:theme', theme.value)
  } catch {
    /* localStorage may be disabled — that's fine. */
  }
}

function onDrawerKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && drawerOpen.value) closeDrawer()
}

onMounted(() => {
  try {
    const stored = localStorage.getItem('cs:theme')
    if (stored === 'dark' || stored === 'light') theme.value = stored
  } catch {
    /* ignore */
  }
  applyTheme()
  window.addEventListener('keydown', onDrawerKeydown)
  refreshPluginsForProject(currentProjectId.value)
})

const removeCatalogRefreshHook = router.afterEach((to) => {
  refreshPluginsForProject(parseProjectId(to.params.id) ?? activeProject.value?.id ?? null)
})

onBeforeUnmount(() => {
  removeCatalogRefreshHook()
  window.removeEventListener('keydown', onDrawerKeydown)
})

const projectNavSections = computed<StackOsNavSection[]>(() => {
  const id = currentProjectId.value
  if (!id) return []
  return buildProjectNavSections(id, enabledPlugins.value)
})

const routeViewKey = computed(() =>
  routeProjectId.value === null ? route.path : `project:${routeProjectId.value}`,
)

function refreshPluginsForProject(projectId: number | null): void {
  if (projectId === lastCatalogProjectId.value) return
  lastCatalogProjectId.value = projectId
  if (projectId) void catalog.refreshPlugins(projectId, { silent: true })
}

function closeDrawer(): void {
  drawerOpen.value = false
}

function dismissToast(id: number): void {
  toasts.dismiss(id)
}

const TOAST_TONE = {
  error: {
    icon: 'x-circle',
    iconClass: 'text-danger',
    barClass: 'bg-danger',
  },
  success: {
    icon: 'check-circle',
    iconClass: 'text-success',
    barClass: 'bg-success',
  },
  info: {
    icon: 'info',
    iconClass: 'text-info',
    barClass: 'bg-info',
  },
} as const

function toastTone(kind: string) {
  return TOAST_TONE[kind as keyof typeof TOAST_TONE] ?? TOAST_TONE.info
}

const isAuthErrorRoute = computed(() => route.name === 'auth-error')
</script>

<template>
  <div class="flex min-h-screen flex-col bg-bg-app text-fg-default md:flex-row">
    <a
      href="#cs-main"
      class="sr-only z-toast rounded-sm bg-accent px-3 py-2 text-sm font-medium text-fg-on-accent focus:not-sr-only focus:fixed focus:left-3 focus:top-3"
    >
      Skip to content
    </a>

    <!-- Mobile top bar -->
    <div class="sticky top-0 z-sticky flex h-14 items-center justify-between gap-3 border-b border-sb-border bg-sb-bg px-3 md:hidden">
      <div class="flex min-w-0 items-center gap-2.5">
        <img
          src="/favicon.png"
          alt=""
          class="h-8 w-8 shrink-0 rounded-md"
          aria-hidden="true"
        >
        <div class="min-w-0">
          <div class="truncate text-sm font-semibold leading-tight text-sb-strong">
            StackOS
          </div>
          <div class="truncate text-2xs leading-tight text-sb-muted">
            {{ currentProject?.name ?? 'Projects' }}
          </div>
        </div>
      </div>
      <button
        type="button"
        class="focus-ring-sb inline-flex h-9 w-9 items-center justify-center rounded-sm text-sb-fg transition-colors duration-fast hover:bg-sb-hover"
        :aria-expanded="drawerOpen"
        aria-controls="cs-sidebar"
        aria-label="Toggle navigation"
        @click="drawerOpen = !drawerOpen"
      >
        <UiIcon
          :name="drawerOpen ? 'close' : 'menu'"
          class="h-5 w-5"
          aria-hidden="true"
        />
      </button>
    </div>

    <!-- Mobile drawer scrim -->
    <button
      v-if="drawerOpen"
      type="button"
      class="fixed inset-0 z-overlay bg-bg-overlay md:hidden"
      aria-label="Close navigation"
      tabindex="-1"
      @click="closeDrawer"
    />

    <!-- Sidebar -->
    <aside
      id="cs-sidebar"
      :class="[
        'bg-sb-bg md:sticky md:top-0 md:z-auto md:h-screen md:w-sidebar md:flex-shrink-0 md:translate-x-0 md:shadow-none',
        'fixed inset-y-0 left-0 z-modal w-[280px] max-w-[85vw] transition-transform duration-base ease-standard md:static',
        drawerOpen ? 'translate-x-0 shadow-xl' : '-translate-x-full',
      ]"
      aria-label="Primary navigation"
    >
      <div class="flex h-full flex-col">
        <div class="hidden px-3 pb-2 pt-3 md:block">
          <RouterLink
            to="/"
            class="focus-ring-sb flex items-center gap-2.5 rounded-md px-1.5 py-1.5 transition-colors duration-fast hover:bg-sb-hover"
            aria-label="StackOS home"
          >
            <img
              src="/favicon.png"
              alt=""
              class="h-8 w-8 shrink-0 rounded-md"
              aria-hidden="true"
            >
            <div class="min-w-0">
              <div class="truncate text-sm font-semibold leading-tight text-sb-strong">
                StackOS
              </div>
              <div class="text-2xs leading-tight text-sb-muted">
                Local runtime
              </div>
            </div>
          </RouterLink>
        </div>

        <div class="px-3 py-3 pt-4 md:pt-3">
          <ProjectSwitcher />
        </div>

        <nav class="scrollbar-dark min-h-0 flex-1 overflow-y-auto px-3 pb-3 pt-1">
          <p
            v-if="projectNavSections.length === 0"
            class="rounded-md border border-dashed border-sb-border px-3 py-3 text-sm text-sb-muted"
          >
            Pick a project to see its operating navigation.
          </p>
          <PluginNavRenderer
            v-else
            :sections="projectNavSections"
            @click.capture="closeDrawer"
          />
        </nav>

        <div class="flex items-center justify-between gap-2 border-t border-sb-border px-4 py-3">
          <span class="text-2xs text-sb-muted">{{ theme === 'dark' ? 'Dark' : 'Light' }} theme</span>
          <button
            type="button"
            class="focus-ring-sb inline-flex h-7 w-7 items-center justify-center rounded-sm text-sb-fg transition-colors duration-fast hover:bg-sb-hover hover:text-sb-strong"
            :aria-pressed="theme === 'dark'"
            :aria-label="theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'"
            @click="toggleTheme"
          >
            <UiIcon
              :name="theme === 'dark' ? 'sun' : 'moon'"
              class="h-4 w-4"
              aria-hidden="true"
            />
          </button>
        </div>
      </div>
    </aside>

    <main
      id="cs-main"
      class="relative min-w-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6 lg:px-8 lg:py-6"
    >
      <ul
        v-if="toastItems.length > 0"
        class="pointer-events-none fixed inset-x-4 top-3 z-toast mx-auto flex max-w-sm flex-col gap-2 sm:left-auto sm:right-4 sm:mx-0"
        aria-live="polite"
      >
        <li
          v-for="t in toastItems"
          :key="t.id"
          role="status"
          class="toast-enter pointer-events-auto relative overflow-hidden rounded-lg border border-default bg-bg-surface p-3 pl-4 text-sm shadow-lg"
        >
          <span
            :class="['absolute inset-y-0 left-0 w-1', toastTone(t.kind).barClass]"
            aria-hidden="true"
          />
          <div class="flex items-start gap-2.5">
            <UiIcon
              :name="toastTone(t.kind).icon"
              :class="['mt-0.5 h-4 w-4 shrink-0', toastTone(t.kind).iconClass]"
              aria-hidden="true"
            />
            <div class="min-w-0 flex-1">
              <div class="font-medium text-fg-strong">
                {{ t.title }}
              </div>
              <div
                v-if="t.detail"
                class="mt-0.5 text-xs text-fg-muted"
              >
                {{ t.detail }}
              </div>
            </div>
            <button
              type="button"
              class="focus-ring -m-1 rounded-sm p-1 text-fg-subtle transition-colors duration-fast hover:text-fg-default"
              :aria-label="`Dismiss ${t.title}`"
              @click="dismissToast(t.id)"
            >
              <UiIcon
                name="close"
                class="h-3.5 w-3.5"
                aria-hidden="true"
              />
            </button>
          </div>
        </li>
      </ul>

      <div
        v-if="!auth.ready && !isAuthErrorRoute"
        class="mb-4 flex items-center gap-2.5 rounded-lg border border-warning-border bg-warning-subtle p-3 text-sm text-warning-fg"
        role="alert"
      >
        <UiIcon
          name="loader"
          class="h-4 w-4 shrink-0 animate-spin"
          aria-hidden="true"
        />
        Authenticating with the daemon…
      </div>

      <RouterView :key="routeViewKey" />
    </main>
  </div>
</template>

<style scoped>
.toast-enter {
  animation: toast-slide-in var(--duration-base) var(--easing-enter);
}

@keyframes toast-slide-in {
  from {
    opacity: 0;
    transform: translateY(-4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
