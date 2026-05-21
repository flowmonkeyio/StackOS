<script setup lang="ts">
import { RouterLink, useRoute } from 'vue-router'

import type { StackOsNavSection } from '@/lib/stackos/nav'

defineProps<{
  sections: StackOsNavSection[]
}>()

const route = useRoute()

function activeClass(item: { to: string; matchPrefix?: boolean }): string {
  const active = item.matchPrefix
    ? route.path === item.to || route.path.startsWith(`${item.to}/`)
    : route.path === item.to
  return active
    ? 'bg-accent-subtle text-accent-fg'
    : 'text-fg-default hover:bg-bg-surface-alt'
}
</script>

<template>
  <div class="space-y-3">
    <section
      v-for="section in sections"
      :key="section.key"
      :aria-label="`${section.label} navigation`"
    >
      <h2 class="mb-0.5 px-3 text-[11px] font-semibold uppercase tracking-wide text-fg-subtle">
        {{ section.label }}
      </h2>
      <ul class="space-y-0.5">
        <li
          v-for="item in section.items"
          :key="item.key"
        >
          <RouterLink
            :to="item.to"
            :title="item.description"
            :class="[
              'relative block rounded-md px-3 py-1.5 text-sm transition-colors duration-fast focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus',
              activeClass(item),
            ]"
          >
            <span class="block min-w-0 truncate pl-2 font-medium">
              {{ item.label }}
            </span>
          </RouterLink>
        </li>
      </ul>
    </section>
  </div>
</template>
