<script setup lang="ts">
import { useRoute } from 'vue-router'
import UiIcon from '@/components/ui/UiIcon.vue'
import { hasIcon } from '@/components/ui/icons'
import { isStackOsNavItemActive, type StackOsNavItem, type StackOsNavSection } from '@/lib/stackos/nav'

defineProps<{ sections: StackOsNavSection[] }>()
const route = useRoute()
function isItemActive(item: StackOsNavItem): boolean {
  return isStackOsNavItemActive(item, route.path, route.query)
}
</script>

<template>
  <div class="space-y-5">
    <section
      v-for="section in sections"
      :key="section.key"
      :aria-label="`${section.label || 'Project'} navigation`"
    >
      <h2
        v-if="section.label"
        class="t-overline mb-1 px-2 text-sb-muted"
      >
        {{ section.label }}
      </h2>
      <ul class="space-y-px">
        <li
          v-for="item in section.items"
          :key="item.key"
        >
          <RouterLink
            :to="item.to"
            :title="item.description || item.label"
            :aria-current="isItemActive(item) ? 'page' : undefined"
            :class="[
              'focus-ring-sb group flex h-8 items-center gap-2.5 rounded-sm px-2 text-sm font-medium transition-colors duration-fast',
              isItemActive(item) ? 'bg-sb-active text-sb-strong' : 'text-sb-fg hover:bg-sb-hover hover:text-sb-strong',
            ]"
          >
            <UiIcon
              v-if="hasIcon(item.icon)"
              :name="item.icon"
              :class="['h-4 w-4 shrink-0', isItemActive(item) ? 'text-sb-accent' : 'text-sb-muted group-hover:text-sb-fg']"
              aria-hidden="true"
            />
            <span class="min-w-0 truncate">{{ item.label }}</span>
          </RouterLink>
        </li>
      </ul>
    </section>
  </div>
</template>
