<!--
  PluginNavRenderer — sidebar navigation for core + plugin-contributed
  sections, rendered against the dark sidebar material (`sb-*` tokens).

  Core sections render as flat groups with an overline label. Plugin
  sections (`section.collapsible`) render as disclosure groups: collapsed by
  default, expanded when they contain the active route or when the user
  toggles them (persisted to localStorage).
-->
<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import UiIcon from '@/components/ui/UiIcon.vue'
import { hasIcon } from '@/components/ui/icons'
import { isStackOsNavItemActive, type StackOsNavItem, type StackOsNavSection } from '@/lib/stackos/nav'

const props = defineProps<{
  sections: StackOsNavSection[]
}>()

const route = useRoute()

const STORAGE_KEY = 'cs:nav-groups'

function readStoredToggles(): Record<string, boolean> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
      return parsed as Record<string, boolean>
    }
  } catch {
    /* localStorage may be unavailable — fall through. */
  }
  return {}
}

const userToggles = ref<Record<string, boolean>>(readStoredToggles())

function isItemActive(item: StackOsNavItem): boolean {
  return isStackOsNavItemActive(item, route.path, route.query)
}

function sectionHasActiveItem(section: StackOsNavSection): boolean {
  return section.items.some((item) => isItemActive(item))
}

const openByKey = computed<Record<string, boolean>>(() => {
  const map: Record<string, boolean> = {}
  for (const section of props.sections) {
    if (!section.collapsible) {
      map[section.key] = true
      continue
    }
    map[section.key] = userToggles.value[section.key] ?? sectionHasActiveItem(section)
  }
  return map
})

function toggleSection(section: StackOsNavSection): void {
  const next = { ...userToggles.value, [section.key]: !openByKey.value[section.key] }
  userToggles.value = next
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
  } catch {
    /* ignore */
  }
}

type NavBlock =
  | { type: 'flat'; key: string; section: StackOsNavSection }
  | { type: 'plugins'; key: string; sections: StackOsNavSection[] }

/** Preserve section order; fold consecutive plugin sections into one block. */
const blocks = computed<NavBlock[]>(() => {
  const out: NavBlock[] = []
  for (const section of props.sections) {
    if (section.collapsible) {
      const last = out[out.length - 1]
      if (last?.type === 'plugins') last.sections.push(section)
      else out.push({ type: 'plugins', key: `plugins-${section.key}`, sections: [section] })
    } else {
      out.push({ type: 'flat', key: section.key, section })
    }
  }
  return out
})
</script>

<template>
  <div class="space-y-5">
    <template v-for="block in blocks">
      <section
        v-if="block.type === 'flat'"
        :key="block.key"
        :aria-label="`${block.section.label} navigation`"
      >
        <h2 class="t-overline mb-1 px-2 text-sb-muted">
          {{ block.section.label }}
        </h2>
        <ul class="space-y-px">
          <li
            v-for="item in block.section.items"
            :key="item.key"
          >
            <RouterLink
              :to="item.to"
              :title="item.description"
              :class="[
                'focus-ring-sb group flex h-8 items-center gap-2.5 rounded-sm px-2 text-sm font-medium transition-colors duration-fast',
                isItemActive(item)
                  ? 'bg-sb-active text-sb-strong'
                  : 'text-sb-fg hover:bg-sb-hover hover:text-sb-strong',
              ]"
            >
              <UiIcon
                v-if="hasIcon(item.icon)"
                :name="item.icon"
                :class="[
                  'h-4 w-4 shrink-0 transition-colors duration-fast',
                  isItemActive(item) ? 'text-sb-accent' : 'text-sb-muted group-hover:text-sb-fg',
                ]"
                aria-hidden="true"
              />
              <span class="min-w-0 truncate">{{ item.label }}</span>
            </RouterLink>
          </li>
        </ul>
      </section>

      <section
        v-else
        :key="`${block.key}-plugins`"
        aria-label="Plugin navigation"
      >
        <h2 class="t-overline mb-1 px-2 text-sb-muted">
          Plugins
        </h2>
        <ul class="space-y-px">
          <li
            v-for="section in block.sections"
            :key="section.key"
          >
            <button
              type="button"
              :class="[
                'focus-ring-sb flex h-8 w-full items-center gap-2.5 rounded-sm px-2 text-sm font-medium transition-colors duration-fast',
                sectionHasActiveItem(section) && !openByKey[section.key]
                  ? 'bg-sb-active text-sb-strong'
                  : 'text-sb-fg hover:bg-sb-hover hover:text-sb-strong',
              ]"
              :aria-expanded="openByKey[section.key]"
              @click="toggleSection(section)"
            >
              <UiIcon
                v-if="hasIcon(section.icon)"
                :name="section.icon"
                class="h-4 w-4 shrink-0 text-sb-muted"
                aria-hidden="true"
              />
              <span class="min-w-0 flex-1 truncate text-left">{{ section.label }}</span>
              <UiIcon
                name="chevron-right"
                :class="[
                  'h-3.5 w-3.5 shrink-0 text-sb-muted transition-transform duration-fast',
                  openByKey[section.key] && 'rotate-90',
                ]"
                aria-hidden="true"
              />
            </button>
            <ul
              v-if="openByKey[section.key]"
              class="mt-px space-y-px"
            >
              <li
                v-for="item in section.items"
                :key="item.key"
              >
                <RouterLink
                  :to="item.to"
                  :title="item.description"
                  :class="[
                    'focus-ring-sb relative flex h-7 items-center rounded-sm py-1 pl-[34px] pr-2 text-sm transition-colors duration-fast',
                    'before:absolute before:bottom-1 before:left-[15px] before:top-1 before:w-px before:bg-sb-border',
                    isItemActive(item)
                      ? 'bg-sb-active font-medium text-sb-strong before:bg-sb-accent'
                      : 'text-sb-fg hover:bg-sb-hover hover:text-sb-strong',
                  ]"
                >
                  <span class="min-w-0 truncate">{{ item.label }}</span>
                </RouterLink>
              </li>
            </ul>
          </li>
        </ul>
      </section>
    </template>
  </div>
</template>
