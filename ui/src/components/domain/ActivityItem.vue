<!--
  ActivityItem — one row in the project activity/timeline feed.

  Renders a durable ProjectEventOut as a calm, human-readable line: a tone
  medallion (icon + severity from the event taxonomy), a plain-language title and
  summary, and compact meta (category · actor · relative time) with optional
  tags. When `to` is provided the whole row is a RouterLink into the relevant
  run/task. Composed from existing primitives (UiIcon, UiBadge) — no bespoke
  status logic; the taxonomy lives in lib/stackos/events.ts.
-->
<script setup lang="ts">
import { computed } from 'vue'

import UiBadge from '@/components/ui/UiBadge.vue'
import UiIcon from '@/components/ui/UiIcon.vue'
import type { Tone } from '@/design/status'
import { eventActor, humanizeEvent, resolveEventVisual } from '@/lib/stackos/events'
import { formatAbsoluteDateTime, formatRelativeDateTime } from '@/lib/stackos/time'
import type { SchemaProjectEventOut } from '@/api'

const props = withDefaults(
  defineProps<{
    event: SchemaProjectEventOut
    /** In-app route the row links to (e.g. a run or task). */
    to?: string | null
    /** Tighter spacing for dense lists. */
    compact?: boolean
  }>(),
  { to: null, compact: false },
)

const MEDALLION: Record<Tone, string> = {
  neutral: 'bg-bg-surface-alt text-fg-muted',
  info: 'bg-info-subtle text-info-fg',
  success: 'bg-success-subtle text-success-fg',
  warning: 'bg-warning-subtle text-warning-fg',
  danger: 'bg-danger-subtle text-danger-fg',
}

const visual = computed(() => resolveEventVisual(props.event))
const human = computed(() => humanizeEvent(props.event))
const actor = computed(() => eventActor(props.event))
const when = computed(() => props.event.occurred_at ?? props.event.created_at ?? null)
const tags = computed(() => (props.event.tags ?? []).slice(0, 3))
const medallionClass = computed(() => MEDALLION[visual.value.tone])
</script>

<template>
  <component
    :is="to ? 'RouterLink' : 'div'"
    :to="to ?? undefined"
    :class="[
      'activity-item group flex w-full items-start gap-3 text-left',
      compact ? 'px-4 py-2.5' : 'px-4 py-3',
      to
        ? 'focus-ring-inset cursor-pointer transition-colors duration-fast hover:bg-bg-surface-alt'
        : '',
    ]"
  >
    <span
      :class="[
        'mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full',
        medallionClass,
      ]"
      aria-hidden="true"
    >
      <UiIcon
        :name="visual.icon"
        class="h-4 w-4"
      />
    </span>

    <div class="min-w-0 flex-1">
      <div class="flex items-start justify-between gap-3">
        <p class="min-w-0 truncate text-sm font-medium text-fg-strong">
          {{ human.title }}
        </p>
        <span
          v-if="when"
          class="shrink-0 text-2xs text-fg-subtle tabular-nums"
          :title="formatAbsoluteDateTime(when)"
        >
          {{ formatRelativeDateTime(when) }}
        </span>
      </div>

      <p
        v-if="human.summary"
        class="mt-0.5 line-clamp-2 text-sm text-fg-muted"
      >
        {{ human.summary }}
      </p>

      <div class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-2xs text-fg-subtle">
        <span>{{ visual.label }}</span>
        <template v-if="actor">
          <span aria-hidden="true">·</span>
          <span class="truncate">{{ actor }}</span>
        </template>
        <UiBadge
          v-for="tag in tags"
          :key="tag"
          tone="neutral"
          variant="subtle"
          size="sm"
        >
          {{ tag }}
        </UiBadge>
      </div>
    </div>

    <UiIcon
      v-if="to"
      name="chevron-right"
      class="mt-1.5 h-4 w-4 shrink-0 text-fg-subtle opacity-0 transition-opacity duration-fast group-hover:opacity-100"
      aria-hidden="true"
    />
  </component>
</template>
