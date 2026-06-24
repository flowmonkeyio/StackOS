<!--
  AttentionItemRow — one "needs you" row, shared by Home's attention band and
  the Inbox. Renders a derived AttentionItem (stores/attention.ts) as a calm
  line: tone medallion, title + detail, relative time, and a single CTA that
  routes to where the human acts. Composed from existing primitives.
-->
<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'

import UiButton from '@/components/ui/UiButton.vue'
import UiIcon from '@/components/ui/UiIcon.vue'
import type { Tone } from '@/design/status'
import { formatAbsoluteDateTime, formatRelativeDateTime } from '@/lib/stackos/time'
import type { AttentionItem, AttentionKind } from '@/stores/attention'

const props = defineProps<{ item: AttentionItem }>()
const router = useRouter()

const MEDALLION: Record<Tone, string> = {
  neutral: 'bg-bg-surface-alt text-fg-muted',
  info: 'bg-info-subtle text-info-fg',
  success: 'bg-success-subtle text-success-fg',
  warning: 'bg-warning-subtle text-warning-fg',
  danger: 'bg-danger-subtle text-danger-fg',
}

const KIND_ICON: Record<AttentionKind, string> = {
  'failed-run': 'x-circle',
  question: 'inbox',
  blocked: 'octagon-alert',
  connection: 'plug',
  budget: 'banknotes',
}

const medallionClass = computed(() => MEDALLION[props.item.tone])
const icon = computed(() => KIND_ICON[props.item.kind])
</script>

<template>
  <div class="flex items-start gap-3 px-4 py-3">
    <span
      :class="[
        'mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full',
        medallionClass,
      ]"
      aria-hidden="true"
    >
      <UiIcon
        :name="icon"
        class="h-4 w-4"
      />
    </span>

    <div class="min-w-0 flex-1">
      <div class="flex items-start justify-between gap-3">
        <p class="min-w-0 text-sm font-medium text-fg-strong">
          {{ item.title }}
        </p>
        <span
          v-if="item.when"
          class="shrink-0 text-2xs text-fg-subtle tabular-nums"
          :title="formatAbsoluteDateTime(item.when)"
        >
          {{ formatRelativeDateTime(item.when) }}
        </span>
      </div>
      <p
        v-if="item.detail"
        class="mt-0.5 line-clamp-2 text-sm text-fg-muted"
      >
        {{ item.detail }}
      </p>
    </div>

    <UiButton
      variant="secondary"
      size="sm"
      class="mt-0.5 shrink-0"
      @click="router.push(item.to)"
    >
      {{ item.cta }}
    </UiButton>
  </div>
</template>
