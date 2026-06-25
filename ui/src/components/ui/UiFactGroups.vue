<!--
  UiFactGroups — grouped semantic facts for drawers and detail summaries.
  Use this when metadata benefits from sectioning, while UiDescriptionList
  remains the smaller primitive for simple ungrouped key/value rows.
-->
<script setup lang="ts">
import UiBadge from './UiBadge.vue'
import type { BadgeTone } from './UiBadge.vue'

export interface UiFactItem {
  label: string
  value?: string | number | boolean | null
  mono?: boolean
  hint?: string
  badge?: boolean
  tone?: BadgeTone
  wide?: boolean
  emphasis?: 'normal' | 'strong'
}

export interface UiFactGroup {
  title: string
  description?: string
  items: UiFactItem[]
}

const props = withDefaults(
  defineProps<{
    groups: UiFactGroup[]
    density?: 'compact' | 'comfortable'
    ariaLabel?: string
  }>(),
  {
    density: 'compact',
    ariaLabel: undefined,
  },
)

function isBlank(value: unknown): boolean {
  return value === null || value === undefined || value === ''
}

function displayValue(value: UiFactItem['value']): string | number {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  return value
}
</script>

<template>
  <div
    class="ui-fact-groups grid gap-3"
    :aria-label="props.ariaLabel"
  >
    <section
      v-for="group in props.groups"
      :key="group.title"
      class="rounded-lg border border-subtle bg-bg-surface-alt"
      :class="props.density === 'compact' ? 'p-3' : 'p-4'"
    >
      <div class="mb-3">
        <h3 class="text-xs font-semibold text-fg-default">
          {{ group.title }}
        </h3>
        <p
          v-if="group.description"
          class="mt-0.5 text-xs text-fg-muted"
        >
          {{ group.description }}
        </p>
      </div>

      <dl class="grid gap-3 sm:grid-cols-2">
        <div
          v-for="item in group.items"
          :key="`${group.title}:${item.label}`"
          class="min-w-0"
          :class="item.wide && 'sm:col-span-2'"
        >
          <dt class="text-xs font-medium text-fg-muted">
            {{ item.label }}
            <span
              v-if="item.hint"
              class="mt-0.5 block text-2xs font-normal text-fg-disabled"
            >
              {{ item.hint }}
            </span>
          </dt>
          <dd
            class="mt-1 min-w-0 break-words"
            :class="[
              item.mono ? 'font-mono text-xs' : 'text-sm',
              item.emphasis === 'strong' ? 'font-semibold text-fg-strong' : 'text-fg-default',
            ]"
          >
            <UiBadge
              v-if="item.badge && !isBlank(item.value)"
              :tone="item.tone ?? 'neutral'"
              variant="subtle"
            >
              {{ displayValue(item.value) }}
            </UiBadge>
            <span v-else>
              {{ displayValue(item.value) }}
            </span>
          </dd>
        </div>
      </dl>
    </section>
  </div>
</template>
